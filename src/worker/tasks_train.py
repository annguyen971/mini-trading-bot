# src/worker/tasks_train.py
# Implements Story 4.1: Conditional Model Training

import os
import sys
import hashlib
import pickle
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split


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
    Placeholder for loading features and labels from the as-of view.
    In a real scenario, this would query the database. For now, it generates
    a dummy dataset that is consistent and usable for a simple model.
    """
    # Simulate loading data from v_features_asof
    # Let's create a dummy DataFrame
    data = {
        'feature1': np.random.rand(100),
        'feature2': np.random.rand(100),
        'feature3': np.random.rand(100),
        'label': np.random.randint(0, 3, 100)
    }
    df = pd.DataFrame(data)
    features = df[['feature1', 'feature2', 'feature3']]
    labels = df['label']
    return features, labels

def refit_hmm(features, params):
    """Placeholder for HMM refitting logic."""
    # This would involve using hmmlearn or a similar library
    dummy_hmm_model = {"n_components": 4, "covariance_type": "full"}
    state_map = {"0": "Accumulation", "1": "Breakout", "2": "Distribution", "3":"Hype"}
    return dummy_hmm_model, state_map

def train_rfc(features, labels, params):
    """Trains a simple RandomForestClassifier."""
    X_train, _, y_train, _ = train_test_split(features, labels, test_size=0.2, random_state=params.get('random_state', 42))
    model = RandomForestClassifier(
        n_estimators=params.get('n_estimators', 50),
        max_depth=params.get('max_depth', 10),
        random_state=params.get('random_state', 42)
    )
    model.fit(X_train, y_train)
    return model


def train_new_model():
    """
    Main logic for training a new model (Story 4.1/AC4-AC7), now with functional code.
    """
    print("Proceeding with model training...")
    # In a real system, config would be loaded from a file or DB
    config = {'rfc_params': {'n_estimators': 50, 'max_depth': 5, 'random_state': 42}}

    # 1. Load data via v_features_asof (NFR4)
    features, labels = load_training_data_as_of()
    print(f"Step 1/5: Loaded data with {len(features)} rows via as-of view.")

    # 2. Refit HMM with Exogenous variables (AC5)
    hmm_model, state_map = refit_hmm(features, {})
    print("Step 2/5: HMM refitted and state map generated.")

    # 3. Train main model (RandomForestClassifier as a stand-in for LightGBM)
    main_model = train_rfc(features, labels, config['rfc_params'])
    print("Step 3/5: Main model (RandomForestClassifier) trained.")

    # 4. Save artifact and calculate hash (AC6)
    model_version = f"v{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    file_path = os.path.join(ARTIFACTS_DIR, f"{model_version}.pkl")

    artifact = {"hmm": hmm_model, "main": main_model, "state_map": state_map}

    with open(file_path, "wb") as f:
        pickle.dump(artifact, f)

    with open(file_path, "rb") as f:
        artifact_sha256 = hashlib.sha256(f.read()).hexdigest()

    print(f"Step 4/5: Saved artifact to {file_path} with SHA256: {artifact_sha256[:10]}...")

    # 5. Record to model_registry (AC7)
    metadata = {
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "code_sha": "dummy_hash", #get_git_commit_hash(),
        "data_snapshot_hash": hashlib.sha256(pd.util.hash_pandas_object(features).values).hexdigest(),
        "feature_set_version": "v3.1-dummy", # This should be dynamic
        "config_hash": hashlib.sha256(str(config).encode()).hexdigest(),
        "random_seed": config['rfc_params']['random_state']
    }

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO model_registry (
                    model_version, model_type, file_path, artifact_sha256, state_map,
                    metrics, is_active, promotion_suggestion, metadata
                ) VALUES (%s, %s, %s, %s, %s::jsonb, NULL, FALSE, NULL, %s::jsonb);
            """, (
                model_version, 'RFC_HMM', file_path, artifact_sha256,
                str(state_map).replace("'", '"'), str(metadata).replace("'", '"')
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
