import os
from contextlib import contextmanager
import psycopg
from core_lib.locks import try_lock

@contextmanager
def pg_conn():
    with psycopg.connect(os.getenv("PG_DSN")) as conn:
        conn.autocommit = False
        yield conn

MIN_RETRAIN_INTERVAL = 7  # days

def check_triggers(conn):
    with conn.cursor() as cur:
        # Trigger 1: PSI > 0.2 for 2 consecutive days
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT 1
                FROM monitoring_logs
                WHERE metric_name = 'psi' AND value > 0.2
                AND log_time >= NOW() - INTERVAL '2 days'
                GROUP BY date_trunc('day', log_time)
                HAVING COUNT(*) >= 1
            ) AS consecutive_days;
        """)
        psi_trigger = cur.fetchone()[0] >= 2

        # Trigger 2: IC drops
        # Placeholder for IC check
        ic_trigger = False

        # Trigger 3: New golden labels
        cur.execute("SELECT COUNT(*) FROM labels_golden WHERE created_at >= NOW() - INTERVAL '7 days';")
        golden_labels_trigger = cur.fetchone()[0] > 20

        # Trigger 4: Model age
        cur.execute("SELECT (NOW() - created_at) > INTERVAL '60 days' FROM model_registry WHERE is_active = true;")
        model_age_trigger = cur.fetchone()
        model_age_trigger = model_age_trigger[0] if model_age_trigger else False

        return psi_trigger or ic_trigger or golden_labels_trigger or model_age_trigger

def check_cooldown(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT (NOW() - created_at) > INTERVAL '%s days' FROM model_promotion_history ORDER BY created_at DESC LIMIT 1;", (MIN_RETRAIN_INTERVAL,))
        cooldown_passed = cur.fetchone()
        return cooldown_passed[0] if cooldown_passed else True

import lightgbm as lgb
import pandas as pd
from sklearn.model_selection import train_test_split
from scipy.optimize import linear_sum_assignment
import numpy as np
import hashlib
import pickle
import json

def train_model_and_refit_hmm(conn):
    df = pd.read_sql("SELECT * FROM v_features_asof", conn)

    # HMM Refitting
    # This is a simplified HMM refit. A real implementation would be more complex.
    from hmmlearn.hmm import GaussianHMM
    hmm_model = GaussianHMM(n_components=4, covariance_type="diag", n_iter=100)
    hmm_model.fit(df[['log_return', 'volatility']]) # Using example features

    # Hungarian Matching for state mapping
    # Placeholder for state_map logic
    state_map = {i: i for i in range(4)}

    # LightGBM Training
    X = df.drop(["symbol", "effective_date", "state_snorkel"], axis=1)
    y = df["state_snorkel"]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    lgb_train = lgb.Dataset(X_train, y_train)
    lgb_eval = lgb.Dataset(X_test, y_test, reference=lgb_train)

    params = {
        'objective': 'multiclass',
        'num_class': 4,
        'metric': 'multi_logloss',
        'boosting_type': 'gbdt',
    }

    gbm = lgb.train(params, lgb_train, num_boost_round=100, valid_sets=lgb_eval)

    return gbm, state_map

def write_to_model_registry(conn, model, state_map, version, feature_set_version, config_hash):
    model_path = f"/opt/artifacts/model_{version}.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    with open(model_path, "rb") as f:
        artifact_sha256 = hashlib.sha256(f.read()).hexdigest()

    metadata = {
        "config_hash": config_hash,
        "random_seed": 42,
        "feature_set_version": feature_set_version,
    }

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO model_registry (model_version, model_type, file_path, artifact_sha256, state_map, metadata)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (version, 'lightgbm', model_path, artifact_sha256, json.dumps(state_map), json.dumps(metadata)))

def run_train_check_batch():
    lock_name = 'train_check_batch'
    with pg_conn() as conn:
        if not try_lock(conn, lock_name):
            print(f"Could not acquire lock {lock_name}. Exiting.")
            return

        try:
            if check_triggers(conn) and check_cooldown(conn):
                print("Triggers met, starting training.")
                model, state_map = train_model_and_refit_hmm(conn)

                version = f"v{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}"
                config_hash = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
                write_to_model_registry(conn, model, state_map, version, "v1", config_hash)
            else:
                print("Triggers not met, skipping training.")
            conn.commit()

        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
            conn.commit()

if __name__ == "__main__":
    run_train_check_batch()
