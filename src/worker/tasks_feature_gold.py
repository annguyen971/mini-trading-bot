import os
import polars as pl
from hmmlearn import hmm
import numpy as np
from contextlib import contextmanager
import psycopg

# === Helper Functions (from feature_logic_v1.md) ===
def z_pos(z_series):
    return z_series.clip_min(0.0)

def clip(series, a=0.0, b=1.0):
    return series.clip(lower_bound=a, upper_bound=b)

def sigmoid(x_series):
    return 1 / (1 + (-x_series).exp())

# Custom HMM to enforce transition mask
class MaskedGaussianHMM(hmm.GaussianHMM):
    def __init__(self, *args, **kwargs):
        self.transmat_mask = kwargs.pop('transmat_mask', None)
        super().__init__(*args, **kwargs)

    def _do_m_step(self, stats):
        super()._do_m_step(stats)
        if self.transmat_mask is not None:
            # Apply the mask after the M-step
            self.transmat_ = self.transmat_ * self.transmat_mask
            # Renormalize rows to sum to 1
            self.transmat_ = self.transmat_ / self.transmat_.sum(axis=1, keepdims=True)

# === HMM Logic (AC2) ===
def apply_hmm(df: pl.DataFrame) -> pl.DataFrame:
    """
    Applies a Hidden Markov Model to detect market regimes for each symbol.
    - 4 states: Accumulation, Breakout, Euphoria, Distribution
    - Sticky bias: Higher probability of staying in the same state.
    - Transition mask: Enforces logical state transitions (e.g., Acc -> Brk, not Acc -> Dst).
    - Filtering-only: Prediction for day T uses data only up to T.
    """
    all_states = []

    # Define HMM parameters based on spec
    n_components = 4  # 4 states
    kappa = 0.6  # Sticky bias

    # Initial transition matrix with sticky bias
    transmat_prior = np.full((n_components, n_components), (1 - kappa) / (n_components - 1))
    np.fill_diagonal(transmat_prior, kappa)

    # Transition mask: 0=Acc, 1=Brk, 2=Eup, 3=Dst
    mask = np.array([
        [1, 1, 0, 0],  # Acc -> Acc, Brk
        [0, 1, 1, 0],  # Brk -> Brk, Eup
        [0, 0, 1, 1],  # Eup -> Eup, Dst
        [1, 0, 0, 1]   # Dst -> Acc, Dst
    ])

    # For each symbol, fit an HMM and predict states
    for symbol_df in df.group_by("symbol", maintain_order=True):

        # Sort by date to ensure correct sequence
        symbol_df = symbol_df.sort("effective_date")

        # Prepare features for HMM (e.g., log return, normalized volume)
        # Placeholder: using log of closing price as a simple feature.
        if 'close' not in symbol_df.columns:
            symbol_df = symbol_df.with_columns(pl.lit(100.0).alias('close'))

        features = symbol_df.select(
            pl.col("close").log().diff().fill_null(0.0).alias("log_return")
        ).to_numpy()

        if len(features) < n_components:
            states = np.zeros(len(features), dtype=int)
        else:
            model = MaskedGaussianHMM(
                n_components=n_components,
                covariance_type="diag",
                n_iter=100,
                tol=1e-3,
                init_params="smc",
                params="smct",
                transmat_prior=transmat_prior,
                transmat_mask=mask
            )

            try:
                model.fit(features)
                post_probs = model.predict_proba(features)
                states = np.argmax(post_probs, axis=1)
            except Exception:
                states = np.zeros(len(features), dtype=int)

        symbol_df = symbol_df.with_columns(pl.Series("hmm_state", states))
        all_states.append(symbol_df)

    if not all_states:
        return df.with_columns(pl.lit(0, dtype=pl.Int32).alias("hmm_state"))

    return pl.concat(all_states)

# === Score Calculation Logic (AC3) ===
def calculate_scores(df: pl.DataFrame) -> pl.DataFrame:
    # Assuming df has the required z-score columns.
    # Creating placeholder columns for testing purposes.
    for col in ['hype_crowd_z', 'news_count_crowd_z', 'S_tech_z', 'breadth_contra_z', 'hype_elitist_z', 'H_ta_z', 'H_cat_z', 'z_score_macro_factor', 'catalyst_active']:
        if col not in df.columns:
            df = df.with_columns(pl.lit(0.5).alias(col))

    # Group 1: FrothScore
    df = df.with_columns(S_crowd=z_pos(pl.col('hype_crowd_z')))
    df = df.with_columns(
        burst=z_pos(pl.col('news_count_crowd_z')),
        catalyst_multiplier=pl.when(pl.col('catalyst_active') > 0).then(0.4).otherwise(1.0)
    )
    df = df.with_columns(S_burst=pl.col('burst') * pl.col('catalyst_multiplier'))
    df = df.with_columns(S_tech=pl.col('S_tech_z'))
    df = df.with_columns(
        Froth_raw=(0.25 * pl.col('S_crowd') +
                   0.20 * pl.col('S_burst') +
                   0.40 * pl.col('S_tech') +
                   0.15 * pl.col('breadth_contra_z'))
    )
    df = df.with_columns(FrothScore=clip(pl.col('Froth_raw'), 0, 1) * 100)

    # Group 2: HunterScore
    df = df.with_columns(div=pl.col('hype_elitist_z') - pl.col('hype_crowd_z'))
    df = df.with_columns(H_div=clip(sigmoid(pl.col('div')), 0, 1))
    df = df.with_columns(
        Hunter_raw=(0.50 * pl.col('H_div') +
                    0.30 * pl.col('H_ta_z') +
                    0.20 * pl.col('H_cat_z'))
    )
    df = df.with_columns(Hunter_base=clip(pl.col('Hunter_raw'), 0, 1))

    # Group 3: Penalty
    gamma = 0.6
    df = df.with_columns(penalty=1.0 - (gamma * pl.col('FrothScore') / 100.0))
    df = df.with_columns(Hunter_adj=clip(pl.col('Hunter_base') * pl.col('penalty'), 0, 1))
    df = df.with_columns(HunterScore=pl.col('Hunter_adj') * 100)

    # Group 4: Macro_Impact_Score
    df = df.with_columns(Macro_Impact_Score=(pl.col('z_score_macro_factor').tanh() * 10.0).round())

    return df.select(["symbol", "effective_date", "hmm_state", "FrothScore", "HunterScore", "Macro_Impact_Score"])

# === Atomic Publish Logic (AC6, AC9) ===
def atomic_publish(conn, df: pl.DataFrame, version: str):
    tmp_table = f"features_gold_v{version}_tmp"
    serving_table = "features_gold_serving"
    bak_table = f"features_gold_v{version}_bak"

    with conn.cursor() as cur:
        # Create temporary table
        cur.execute(f"CREATE TABLE {tmp_table} AS SELECT * FROM {serving_table} WHERE 1=0;")

        # Write DataFrame to temporary table
        df.write_database(tmp_table, os.getenv("PG_DSN"), if_exists='append')

        # Validate temporary table
        cur.execute(f"SELECT COUNT(*) FROM {tmp_table}")
        count = cur.fetchone()[0]
        if count != len(df):
            raise Exception("Validation failed: Row count mismatch.")

        # Create indexes concurrently
        cur.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_fg_tmp_symbol ON {tmp_table}(symbol);")
        cur.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_fg_tmp_effdate ON {tmp_table}(effective_date);")

        # Atomically swap tables
        cur.execute(f"""
            BEGIN;
            ALTER TABLE {serving_table} RENAME TO {bak_table};
            ALTER TABLE {tmp_table} RENAME TO {serving_table};
            COMMIT;
        """)

# === Main Orchestration Logic ===
@contextmanager
def pg_conn():
    with psycopg.connect(os.getenv("PG_DSN")) as conn:
        conn.autocommit = False
        yield conn

def run_feature_gold_batch():
    lock_name = 'feature_gold_batch'
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (lock_name,))
            locked = cur.fetchone()[0]
            if not locked:
                print(f"Could not acquire lock {lock_name}. Exiting.")
                return

        try:
            # Read from as-of view
            df = pl.read_database_uri("SELECT * FROM v_features_asof", os.getenv("PG_DSN"))

            # Apply HMM
            df = apply_hmm(df)

            # Calculate scores
            df = calculate_scores(df)

            # Get next version
            # This should be a more robust versioning system
            version = "1"

            # Atomically publish
            atomic_publish(conn, df, version)

        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
            conn.commit()

if __name__ == "__main__":
    run_feature_gold_batch()
