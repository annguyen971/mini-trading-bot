# src/worker/tasks_train.py

import os
import sys
import hashlib
import json
import pickle
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import lightgbm as lgb
from hmmlearn import hmm
from scipy.optimize import linear_sum_assignment
import psycopg

# Assuming core_lib is installed and accessible
from core_lib.db import pg_conn
from core_lib.locks import get_advisory_lock

# --- Constants ---
MODEL_ARTIFACTS_DIR = os.getenv("MODEL_ARTIFACTS_DIR", "artifacts/")
MIN_RETRAIN_INTERVAL_DAYS = 7


def check_triggers(conn):
    """Checks the four triggers to decide if retraining is needed."""
    triggers = {
        'psi': False,
        'ic_drift': False,
        'new_labels': False,
        'model_age': False,
    }

    with conn.cursor() as cur:
        # Trigger 1: PSI > 0.2 in last 2 days
        cur.execute("""
            SELECT COUNT(*) FROM monitoring_logs
            WHERE metric_name = 'psi_top_10' AND value > 0.2
            AND log_time >= NOW() - INTERVAL '2 days';
        """)
        if cur.fetchone()[0] >= 2:
            triggers['psi'] = True

        # Trigger 2: IC drift
        # This is a simplified check. A real one might compare IC values directly.
        cur.execute("""
            SELECT value FROM monitoring_logs
            WHERE metric_name = 'ic_drift_alert' AND log_time >= NOW() - INTERVAL '7 days'
            ORDER BY log_time DESC LIMIT 1;
        """)
        # Assuming the log is 1 if drift is detected
        if cur.rowcount > 0 and cur.fetchone()[0] == 1.0:
            triggers['ic_drift'] = True

        # Trigger 3: New golden labels
        cur.execute("""
            SELECT COUNT(*) FROM labels_golden WHERE created_at >= NOW() - INTERVAL '7 days';
        """)
        if cur.fetchone()[0] > 20:
            triggers['new_labels'] = True

        # Trigger 4: Model Age > 60 days
        cur.execute("""
            SELECT created_at FROM model_registry WHERE is_active = TRUE ORDER BY created_at DESC LIMIT 1;
        """)
        if cur.rowcount > 0:
            last_active_model_date = cur.fetchone()[0]
            if (datetime.now(last_active_model_date.tzinfo) - last_active_model_date).days > 60:
                triggers['model_age'] = True
        else: # No active model, so we should train one
            triggers['model_age'] = True

    print(f"Triggers check result: {triggers}")
    return any(triggers.values())

def fetch_training_data(conn):
    """Fetches features and labels, prioritizing golden labels."""
    print("Fetching features from the last 3 years...")
    features_query = "SELECT * FROM v_features_asof WHERE effective_date >= NOW() - INTERVAL '3 years';"
    features_df = pd.read_sql(features_query, conn)

    print("Fetching silver and golden labels...")
    labels_query = """
        WITH latest_golden AS (
            SELECT DISTINCT ON (symbol, effective_date)
                symbol, effective_date, new_label AS final_label, 1 AS is_golden
            FROM labels_golden
            ORDER BY symbol, effective_date, created_at DESC
        )
        SELECT
            s.symbol,
            s.effective_date,
            COALESCE(g.final_label, s.state_snorkel) AS final_label,
            COALESCE(g.is_golden, 0) AS is_golden
        FROM labels_silver s
        LEFT JOIN latest_golden g ON s.symbol = g.symbol AND s.effective_date = g.effective_date
        WHERE s.effective_date >= NOW() - INTERVAL '3 years';
    """
    labels_df = pd.read_sql(labels_query, conn)

    return features_df, labels_df

def retrain_hmm_with_exogenous(df, features):
    """
    Trains a GaussianHMM model where exogenous features influence emissions.
    This is a simplified approach; a real implementation might use a more
    advanced HMM package that formally supports exogenous variables. Here,
    we concatenate them as if they are standard features.
    """
    df_hmm = df[features].dropna()
    model = hmm.GaussianHMM(n_components=4, covariance_type="diag", n_iter=100, random_state=42)
    model.fit(df_hmm.values)
    return model

def train_main_model(df, features, label_col):
    """Trains a LightGBM classification model."""
    df_model = df[features + [label_col]].dropna()

    X = df_model[features]
    y = df_model[label_col]

    lgb_train = lgb.Dataset(X, y)

    params = {
        'objective': 'multiclass',
        'num_class': 4,
        'metric': 'multi_logloss',
        'boosting_type': 'gbdt',
        'num_leaves': 31,
        'learning_rate': 0.05,
        'feature_fraction': 0.9,
        'seed': 123,
        'verbose': -1
    }

    gbm = lgb.train(params, lgb_train, num_boost_round=200)
    return gbm

def get_state_map_hungarian(conn, new_hmm, df, features):
    """
    Determines the mapping from new HMM states to the canonical ordering of the
    last active model using the Hungarian algorithm.
    """
    # 1. Get the last active model's HMM and its state means
    old_hmm_model = None
    old_state_map = None
    with conn.cursor() as cur:
        cur.execute("""
            SELECT file_path, state_map FROM model_registry
            WHERE is_active = TRUE ORDER BY created_at DESC LIMIT 1;
        """)
        if cur.rowcount > 0:
            old_model_path, old_state_map_json = cur.fetchone()
            if os.path.exists(old_model_path):
                with open(old_model_path, 'rb') as f:
                    old_model_bundle = pickle.load(f)
                    old_hmm_model = old_model_bundle.get('hmm')
                    old_state_map = json.loads(old_state_map_json)
            else:
                print(f"Warning: Could not find old model artifact at {old_model_path}")

    if old_hmm_model is None or old_state_map is None:
        print("No active model found or artifact missing. Creating a default sort-based state map.")
        # Fallback to the simple sorting method if no old model is available
        new_means = new_hmm.means_
        sorted_indices = np.argsort(new_means[:, 0])
        # Default mapping: lowest vol=Acc(0), lowish vol=Dist(2), highish vol=Burst(1), highest vol=Neutral(3)
        return {sorted_indices[0]: 0, sorted_indices[1]: 2, sorted_indices[2]: 1, sorted_indices[3]: 3}

    # 2. Re-order old means to canonical order (0, 1, 2, 3) before comparison
    old_means_canonical = np.zeros_like(old_hmm_model.means_)
    for old_state_idx, canonical_state in old_state_map.items():
        old_means_canonical[canonical_state] = old_hmm_model.means_[int(old_state_idx)]

    new_means = new_hmm.means_

    # 3. Build the cost matrix using Euclidean distance
    cost_matrix = np.zeros((4, 4))
    for i in range(4): # Canonical states (from old model)
        for j in range(4): # New model's raw states
            cost_matrix[i, j] = np.linalg.norm(old_means_canonical[i] - new_means[j])

    # 4. Use the Hungarian algorithm to find the optimal assignment
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    # 5. Create the new state map {new_raw_state: canonical_state}
    state_map = {int(col): int(row) for row, col in zip(row_ind, col_ind)}

    return state_map

def register_model(conn, model_version, file_path, artifact_sha256, state_map, feature_set_version):
    """Inserts the new model metadata into the model_registry table."""

    state_map_json = json.dumps({int(k): int(v) for k, v in state_map.items()}) # Ensure keys are JSON compatible

    # Metadata can include training parameters, code version, etc.
    metadata = {
        "training_date": datetime.utcnow().isoformat(),
        "feature_set_version": feature_set_version,
        "lgbm_params": {
            'num_leaves': 31,
            'learning_rate': 0.05,
        }
    }

    insert_query = """
        INSERT INTO model_registry (
            model_version, model_type, file_path, artifact_sha256, state_map,
            metrics, is_active, promotion_suggestion, metadata, created_at
        ) VALUES (
            %s, %s, %s, %s, %s::jsonb,
            NULL, FALSE, 'awaiting_backtest', %s::jsonb, NOW()
        );
    """

    with conn.cursor() as cur:
        cur.execute(insert_query, (
            model_version,
            'lgbm_hmm_v2',
            file_path,
            artifact_sha256,
            state_map_json,
            json.dumps(metadata)
        ))
    print(f"Successfully registered model: {model_version}")

def run_train_check_batch():
    """
    Checks if any retraining triggers are met and, if so, runs the full
    training and registration pipeline.
    """
    lock_name = "train_check_batch"
    print(f"Attempting to acquire lock: {lock_name}")

    with pg_conn() as conn:
        if not get_advisory_lock(conn, lock_name):
            print(f"Could not acquire lock '{lock_name}'. Exiting.")
            return 0

        print("Lock acquired. Starting train_check_batch.")

        try:
            # Check cooldown period
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(created_at) FROM model_registry;")
                last_train_time = cur.fetchone()[0]
                if last_train_time and (datetime.now(last_train_time.tzinfo) - last_train_time).days < MIN_RETRAIN_INTERVAL_DAYS:
                    print(f"Cooldown active. Last training was less than {MIN_RETRAIN_INTERVAL_DAYS} days ago. Skipping.")
                    return 0

            # Step 1: Check Triggers (AC2)
            if not check_triggers(conn):
                print("No triggers met. Skipping training.")
                return 0

            print("Triggers met. Proceeding with training.")

            # Step 2: Fetch data (AC4)
            print("Step 2: Fetching data from v_features_asof...")
            features_df, labels_df = fetch_training_data(conn)
            training_df = pd.merge(features_df, labels_df, on=['symbol', 'effective_date'], how='inner')
            training_df = training_df.dropna(subset=['final_label']) # Ensure we only train on labeled data

            print(f"Loaded {len(training_df)} records for training.")

            # Define feature sets
            TA_FEATURES = ['atrp20', 'bb_width', 'rs_21', 'rs_63', 'vol_norm'] # Example TA features
            SA_FEATURES = ['hype_kol', 'divergence_symbol', 'hype_vel_24h'] # Example SA features
            MACRO_FEATURES = ['z_cpi', 'z_ib7d', 'z_fx', 'z_credit'] # Exogenous

            # Step 3: Retrain HMM with Exogenous Variables (AC5)
            print("Step 3: Retraining HMM...")
            hmm_features = TA_FEATURES + MACRO_FEATURES
            hmm_model = retrain_hmm_with_exogenous(training_df, hmm_features)

            # Step 4: Train main model (e.g., LightGBM)
            print("Step 4: Training main model...")
            main_model_features = TA_FEATURES + SA_FEATURES + MACRO_FEATURES
            main_model = train_main_model(training_df, main_model_features, 'final_label')

            # Step 5: Save artifacts and get hash (AC7)
            print("Step 5: Saving artifacts...")
            # Ensure the artifacts directory exists
            os.makedirs(MODEL_ARTIFACTS_DIR, exist_ok=True)

            model_version = f"lgbm_v2_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            file_path = os.path.join(MODEL_ARTIFACTS_DIR, f"{model_version}.pkl")

            with open(file_path, 'wb') as f:
                pickle.dump({'hmm': hmm_model, 'lgbm': main_model}, f)

            with open(file_path, 'rb') as f:
                artifact_sha256 = hashlib.sha256(f.read()).hexdigest()
            print(f"Artifact saved to {file_path} with SHA256: {artifact_sha256}")

            # Step 6: Get state map via Hungarian Matching (AC7)
            print("Step 6: Calculating state map...")
            # Pass the HMM features to the state map function for consistent prediction
            state_map = get_state_map_hungarian(conn, hmm_model, training_df, hmm_features)
            print(f"Calculated state map: {state_map}")

            # Step 7: Write to Model Registry (AC7)
            print("Step 7: Writing new model to registry...")
            # This is a simplified feature_set_version for demonstration
            feature_set_version = f"fs_v1_{hashlib.sha256(','.join(main_model_features).encode()).hexdigest()[:10]}"
            register_model(conn, model_version, file_path, artifact_sha256, state_map, feature_set_version)

            conn.commit()
            print("Training and registration complete.")

        except Exception as e:
            print(f"An error occurred: {e}", file=sys.stderr)
            conn.rollback()
            raise

    return 0

if __name__ == "__main__":
    run_train_check_batch()
