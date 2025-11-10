import os
import pandas as pd
import numpy as np
from contextlib import contextmanager
import psycopg
from core_lib.locks import try_lock

@contextmanager
def pg_conn():
    with psycopg.connect(os.getenv("PG_DSN")) as conn:
        conn.autocommit = False
        yield conn

# Define Abstain and other label constants
ABSTAIN = -1
ACCUMULATION = 0
BREAKOUT = 1
EUPHORIA = 2
DISTRIBUTION = 3

# Labeling Functions (LFs)
from snorkel.labeling import labeling_function

@labeling_function()
def LF_hmm_top1(x):
    return x.hmm_state if x.hmm_state in [ACCUMULATION, BREAKOUT, EUPHORIA, DISTRIBUTION] else ABSTAIN

@labeling_function()
def LF_macro_tailwind(x):
    return BREAKOUT if x.Macro_Impact_Score > 3 else ABSTAIN

@labeling_function()
def LF_macro_shock(x):
    # Assuming macro_shock_flag exists
    return DISTRIBUTION if hasattr(x, 'macro_shock_flag') and x.macro_shock_flag > 0 else ABSTAIN

@labeling_function()
def LF_rule_RSI(x):
    # Assuming rsi_14 exists
    if hasattr(x, 'rsi_14'):
        if x.rsi_14 > 70:
            return EUPHORIA
        elif x.rsi_14 < 30:
            return ACCUMULATION
    return ABSTAIN

def run_label_batch():
    lock_name = 'label_batch'
    with pg_conn() as conn:
        if not try_lock(conn, lock_name):
            print(f"Could not acquire lock {lock_name}. Exiting.")
            return

        try:
            df = pd.read_sql("SELECT * FROM v_features_asof", conn)

            from snorkel.labeling.model import LabelModel
            from snorkel.labeling import PandasLFApplier

            lfs = [LF_hmm_top1, LF_macro_tailwind, LF_macro_shock, LF_rule_RSI]

            applier = PandasLFApplier(lfs=lfs)
            L_train = applier.apply(df=df)

            label_model = LabelModel(cardinality=4, verbose=True)
            label_model.fit(L_train=L_train, n_epochs=500, log_freq=100, seed=123)

            df["state_snorkel"] = label_model.predict(L=L_train, tie_break_policy="abstain")
            df["probability"] = label_model.predict_proba(L=L_train).max(axis=1)

            # Quality Gate
            from snorkel.labeling.analysis import lf_summary
            summary = lf_summary(L_train, lfs=lfs)
            coverage = summary["Coverage"].mean()
            abstain_rate = 1 - coverage
            if coverage < 0.6 or abstain_rate > 0.4:
                raise Exception(f"Quality gate failed: Coverage={coverage}, Abstain Rate={abstain_rate}")

            # Calculate entropy
            probs = label_model.predict_proba(L=L_train)
            entropy = -(probs * np.log(probs + 1e-9)).sum(axis=1)
            df["entropy"] = entropy

            # Write to database
            with conn.cursor() as cur:
                for _, row in df.iterrows():
                    cur.execute("""
                        INSERT INTO labels_silver (symbol, effective_date, state_snorkel, probability, entropy)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (symbol, effective_date) DO UPDATE SET
                            state_snorkel = EXCLUDED.state_snorkel,
                            probability = EXCLUDED.probability,
                            entropy = EXCLUDED.entropy;
                    """, (row['symbol'], row['effective_date'], row['state_snorkel'], row['probability'], row['entropy']))

            # Active Learning Logic
            p90_entropy = df["entropy"].quantile(0.9)
            grey_area = df[
                (df["entropy"] >= p90_entropy) |
                ((df["HunterScore"] >= 80) & (df["FrothScore"] <= 20))
            ]

            # Stratified Sampling
            selected_samples = grey_area.groupby(['sector', 'cap_tercile']).head(1)

            # Add Honeypot on Sundays
            if pd.Timestamp.now().dayofweek == 6:
                honeypot = df.sample(1)
                selected_samples = pd.concat([selected_samples, honeypot])

            # Write to al_queue
            with conn.cursor() as cur:
                for _, row in selected_samples.iterrows():
                    year_week = pd.Timestamp(row['effective_date']).strftime('%Y-%U')
                    dedup_key = f"{row['symbol']}:{row['effective_date']}:{year_week}"
                    cur.execute("""
                        INSERT INTO al_queue (symbol, effective_date, reason, dedup_key)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (dedup_key) DO NOTHING;
                    """, (row['symbol'], row['effective_date'], 'uncertainty', dedup_key))
            conn.commit()

        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
            conn.commit()

if __name__ == "__main__":
    run_label_batch()
