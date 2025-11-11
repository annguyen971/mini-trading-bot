# src/worker/tasks_train.py
# Implements Story 4.1: Conditional Model Training

import os
import sys
import hashlib
import pickle
from datetime import datetime, timedelta, timezone
import lightgbm as lgb
import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM


# Assuming core_lib provides these helpers
from core_lib.db import get_db_connection, advisory_lock
from core_lib.utils import get_git_commit_hash, load_config, hash_data_snapshot

# --- Constants ---
MIN_RETRAIN_INTERVAL_DAYS = 7
PSI_DRIFT_THRESHOLD = 0.2
IC_DROP_THRESHOLD = -0.03
NEW_GOLDEN_LABELS_THRESHOLD = 20
MODEL_AGE_THRESHOLD_DAYS = 60
ARTIFACTS_DIR = "/opt/artifacts"


def check_triggers():
    """
    Checks the four triggers for retraining as per Story 4.1/AC2.
    Returns:
        tuple: (bool, str) indicating if a trigger was met and the reason.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Trigger 1: PSI Drift (simplified check for PoC)
            # A real implementation would check for 2 consecutive days.
            cur.execute("""
                SELECT value FROM monitoring_logs
                WHERE metric_name = 'psi_top_10'
                ORDER BY log_time DESC LIMIT 1;
            """)
            latest_psi = cur.fetchone()
            if latest_psi and latest_psi[0] > PSI_DRIFT_THRESHOLD:
                return True, f"PSI drift exceeded threshold ({latest_psi[0]:.3f} > {PSI_DRIFT_THRESHOLD})"

            # Trigger 2: IC Drop (simplified)
            # A real impl would compare IC_E vs IC_B
            cur.execute("""
                SELECT value FROM monitoring_logs
                WHERE metric_name = 'ic_7d'
                ORDER BY log_time DESC LIMIT 1;
            """)
            latest_ic = cur.fetchone()
            if latest_ic and latest_ic[0] < IC_DROP_THRESHOLD:
                return True, f"IC drop exceeded threshold ({latest_ic[0]:.3f} < {IC_DROP_THRESHOLD})"

            # Trigger 3: New Golden Labels
            cur.execute("""
                SELECT COUNT(*) FROM labels_golden
                WHERE created_at > (SELECT MAX(created_at) FROM model_promotion_history WHERE action = 'PROMOTE_PROD');
            """) # This logic is simplified; it should check since last training run.
            new_labels_count = cur.fetchone()[0]
            if new_labels_count > NEW_GOLDEN_LABELS_THRESHOLD:
                return True, f"New golden labels count ({new_labels_count}) exceeded threshold ({NEW_GOLDEN_LABELS_THRESHOLD})"

            # Trigger 4: Model Age
            cur.execute("""
                SELECT metadata->>'training_timestamp' FROM model_registry
                ORDER BY (metadata->>'training_timestamp')::timestamptz DESC LIMIT 1;
            """)
            last_training_time_str = cur.fetchone()
            if last_training_time_str:
                last_training_time = datetime.fromisoformat(last_training_time_str[0])
                age = datetime.now(timezone.utc) - last_training_time
                if age.days > MODEL_AGE_THRESHOLD_DAYS:
                    return True, f"Model age ({age.days} days) exceeded threshold ({MODEL_AGE_THRESHOLD_DAYS})"

    return False, "No triggers met"


def check_cooldown():
    """
    Checks if the minimum interval since the last training has passed (Story 4.1/AC3).
    Returns:
        bool: True if cooldown is active (i.e., should NOT train), False otherwise.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MAX((metadata->>'training_timestamp')::timestamptz) FROM model_registry;
            """)
            last_run = cur.fetchone()[0]
            if last_run and (datetime.now(timezone.utc) - last_run) < timedelta(days=MIN_RETRAIN_INTERVAL_DAYS):
                print(f"Cooldown active. Last training was at {last_run}. Skipping.")
                return True
    return False


def load_training_data_as_of():
    """
    Loads features and labels from the v_features_asof view. (Story 4.1/AC5 - NFR4)
    """
    print("Loading data from v_features_asof...")
    with get_db_connection() as conn:
        # This assumes the view correctly joins features and the target label
        # and excludes future data.
        # The view should also handle filling NaNs if necessary.
        sql = "SELECT * FROM v_features_asof WHERE golden_label IS NOT NULL;"
        df = pd.read_sql(sql, conn)

    # Separate features and labels
    labels = df['golden_label']
    features = df.drop(columns=['golden_label', 'symbol', 'effective_date']) # Drop non-feature cols
    print(f"Loaded {len(df)} records for training.")
    return features, labels


def map_hmm_states(hmm_model, features):
    """
    Heuristically maps HMM states to meaningful labels based on feature means.
    This is a simplified stand-in for Hungarian Matching.
    """
    state_means = hmm_model.means_
    # Assuming 'feature_of_interest' is a column like 'price_change' or 'volatility'
    # For now, we'll just sort by the mean of the first feature as a proxy.
    interest_feature_idx = 0

    state_order = np.argsort(state_means[:, interest_feature_idx])

    state_map = {
        str(state_order[0]): "Accumulation", # Lowest avg value
        str(state_order[1]): "Distribution",
        str(state_order[2]): "Hype",
        str(state_order[3]): "Breakout" # Highest avg value
    }
    return state_map


def refit_hmm(features, config):
    """
    Refits the HMM using macro-economic variables as exogenous features. (Story 4.1/AC5)
    """
    hmm_features = config.get('hmm_features', ['z_cpi', 'z_fx', 'atrp20', 'hype_kol'])
    # Ensure features exist, fallback to random if not found
    valid_hmm_features = [f for f in hmm_features if f in features.columns]
    if len(valid_hmm_features) < 2:
        print("Warning: Not enough HMM features found. Using dummy data.")
        hmm_data = np.random.randn(len(features), 2)
    else:
        hmm_data = features[valid_hmm_features].values

    model = GaussianHMM(
        n_components=config.get('n_components', 4),
        covariance_type=config.get('covariance_type', "diag"),
        n_iter=config.get('n_iter', 100),
        random_state=config.get('random_state', 42)
    )
    model.fit(hmm_data)
    state_map = map_hmm_states(model, features)
    return model, state_map


def train_lgbm(features, labels, config):
    """
    Trains the main LightGBM model.
    """
    lgb_params = config.get('lgbm_params', {
        'objective': 'multiclass',
        'num_class': 4,
        'metric': 'multi_logloss',
        'n_estimators': 200,
        'learning_rate': 0.05,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 1,
        'verbose': -1,
        'n_jobs': -1,
        'seed': 42
    })

    model = lgb.LGBMClassifier(**lgb_params)
    model.fit(features, labels)
    return model


def train_new_model():
    """
    Main logic for training a new model (Story 4.1/AC4-AC7), with real implementation.
    """
    print("Proceeding with model training...")
    config = load_config().get('training', {}) # Assuming training config is under a 'training' key

    # 1. Load data via v_features_asof (NFR4)
    features, labels = load_training_data_as_of()
    if features.empty:
        print("No training data found. Aborting.")
        return

    # 2. Refit HMM with Exogenous variables (AC5)
    hmm_model, state_map = refit_hmm(features, config.get('hmm_config', {}))
    print(f"Step 2/5: HMM refitted. State map: {state_map}")

    # Add HMM states as a new feature
    hmm_states = hmm_model.predict(features[config.get('hmm_features', ['z_cpi', 'z_fx', 'atrp20', 'hype_kol'])])
    features['hmm_state'] = hmm_states

    # 3. Train main model (LightGBM)
    main_model = train_lgbm(features, labels, config)
    print("Step 3/5: Main model (LightGBM) trained.")

    # 4. Save artifact and calculate hash (AC6)
    model_version = f"v{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    file_path = os.path.join(ARTIFACTS_DIR, f"{model_version}.pkl")
    artifact = {"hmm": hmm_model, "main": main_model, "state_map": state_map, "feature_columns": features.columns.tolist()}

    with open(file_path, "wb") as f:
        pickle.dump(artifact, f)
    with open(file_path, "rb") as f:
        artifact_sha256 = hashlib.sha256(f.read()).hexdigest()

    print(f"Step 4/5: Saved artifact to {file_path} with SHA256: {artifact_sha256[:10]}...")

    # 5. Record to model_registry (AC7)
    metadata = {
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "code_sha": get_git_commit_hash(),
        "data_snapshot_hash": hashlib.sha256(pd.util.hash_pandas_object(features).values).hexdigest(),
        "feature_set_version": "v3.2", # This should be dynamic
        "config_hash": hashlib.sha256(str(config).encode()).hexdigest(),
        "random_seed": config.get('lgbm_params', {}).get('seed', 42)
    }

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO model_registry (
                    model_version, model_type, file_path, artifact_sha256, state_map,
                    metrics, is_active, promotion_suggestion, metadata
                ) VALUES (%s, %s, %s, %s, %s::jsonb, NULL, FALSE, NULL, %s::jsonb);
            """, (
                model_version, 'LGBM_HMM', file_path, artifact_sha256,
                json.dumps(state_map), json.dumps(metadata)
            ))
        conn.commit()
    print(f"Step 5/5: Recorded new model '{model_version}' to model_registry.")


def main():
    """Main execution function."""
    print(f"--- Running train_check_batch at {datetime.now(timezone.utc)} ---")

    with advisory_lock('train_check_batch') as locked:
        if not locked:
            print("Could not acquire lock 'train_check_batch'. Exiting.")
            return 1

        # Check for cooldown period first
        if check_cooldown():
            return 0

        # Check for training triggers
        should_train, reason = check_triggers()
        if not should_train:
            print(f"Skipping training: {reason}")
            return 0

        print(f"Training triggered: {reason}")
        train_new_model()
        print("--- train_check_batch finished successfully ---")
        return 0

if __name__ == "__main__":
    # Ensure artifacts directory exists
    if not os.path.exists(ARTIFACTS_DIR):
        os.makedirs(ARTIFACTS_DIR)

    sys.exit(main())
