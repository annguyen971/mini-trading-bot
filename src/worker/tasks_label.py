# src/worker/tasks_label.py
import hashlib
import logging
import os
import sys
from contextlib import contextmanager
import inspect
import datetime

import numpy as np
import polars as pl
from snorkel.labeling import PandasLFApplier
from snorkel.labeling.model import LabelModel

from core_lib.db import get_db_connection
from core_lib.locks import get_advisory_lock

# --- Constants ---
ABSTAIN = -1
# Define states/labels (example)
TICH_LUY = 0
BUNG_NO = 1
HUNG_PHAN = 2
PHAN_PHOI = 3

# Thresholds from PRD
ABSTAIN_RATE_THRESHOLD = 0.40
COVERAGE_THRESHOLD = 0.60
AUTO_ACCEPT_THRESHOLD = 0.90
WEEKLY_AL_QUOTA = 10

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Labeling Functions (LFs) ---
# Story 3.1/AC2: Implemented with basic logic based on feature names.

def LF_rule_RSI(row):
    """Labels based on RSI indicator."""
    try:
        if row["rsi_14"] < 30:
            return TICH_LUY
        if row["rsi_14"] > 70:
            return HUNG_PHAN
    except KeyError:
        pass
    return ABSTAIN

def LF_hmm_top1(row):
    """Labels based on the top prediction from the HMM model."""
    try:
        return int(row["hmm_state_top1"])
    except (KeyError, ValueError):
        return ABSTAIN

def LF_hmm_duration(row):
    """Labels based on the duration of the current HMM state."""
    try:
        if row["hmm_state_top1"] == TICH_LUY and row["hmm_state_duration"] > 10:
            return TICH_LUY
    except KeyError:
        pass
    return ABSTAIN

def LF_macro_tailwind(row):
    """Labels based on positive macro-economic indicators."""
    try:
        if row["macro_impact_score"] >= 3:  # Tailwind
            return BUNG_NO
    except KeyError:
        pass
    return ABSTAIN

def LF_macro_shock(row):
    """Labels based on negative macro-economic shocks."""
    try:
        if row["macro_shock_flag"] == 1:
            return PHAN_PHOI
    except KeyError:
        pass
    return ABSTAIN

# --- Main Logic Functions ---

def upsert_silver_labels(conn, df_silver_labels: pl.DataFrame):
    """UPSERT silver labels into the database."""
    with conn.cursor() as cursor:
        cursor.execute("CREATE TEMP TABLE silver_labels_temp (LIKE labels_silver)")
        with cursor.copy("COPY silver_labels_temp FROM STDIN") as copy:
            copy.write_polars_csv(df_silver_labels)

        cursor.execute("""
            INSERT INTO labels_silver
            SELECT * FROM silver_labels_temp
            ON CONFLICT (symbol, effective_date) DO UPDATE
            SET state_snorkel = EXCLUDED.state_snorkel,
                probability = EXCLUDED.probability,
                entropy = EXCLUDED.entropy,
                is_auto_accepted = EXCLUDED.is_auto_accepted,
                label_model_version = EXCLUDED.label_model_version,
                lf_fingerprint = EXCLUDED.lf_fingerprint
        """)
        logger.info(f"Upserted {cursor.rowcount} silver labels.")

def run_snorkel_labeling(conn, v_features_asof: pl.DataFrame):
    """
    Applies Labeling Functions, trains a LabelModel, and generates silver labels.
    (Implements Story 3.1)
    """
    logger.info("Starting Snorkel labeling process...")

    # Step 1: Define LFs and create fingerprint (Story 3.1/AC2, AC3)
    lfs = [
        LF_rule_RSI,
        LF_hmm_top1,
        LF_hmm_duration,
        LF_macro_tailwind,
        LF_macro_shock,
    ]

    # Create a fingerprint for reproducibility
    lf_source_code = "".join([inspect.getsource(lf) for lf in lfs])
    lf_fingerprint = hashlib.sha256(lf_source_code.encode("utf-8")).hexdigest()
    logger.info(f"LF fingerprint: {lf_fingerprint}")
    label_model_version = f"snorkel_v1_{lf_fingerprint[:8]}"

    # Step 2: As-of check (Story 3.1/AC7)
    max_date = v_features_asof["effective_date"].max()
    if max_date > datetime.date.today():
        raise ValueError(f"Future data detected in v_features_asof. Max date: {max_date}")

    # Step 2: Apply LFs (Story 3.1/AC3)
    df_pandas = v_features_asof.to_pandas()
    applier = PandasLFApplier(lfs=lfs)
    L_train = applier.apply(df=df_pandas)

    # Step 3: Train LabelModel and check quality (Story 3.1/AC3, AC5)
    label_model = LabelModel(cardinality=4, verbose=False)
    label_model.fit(L_train=L_train, n_epochs=100, log_freq=20, seed=42)

    conflicts = (L_train != ABSTAIN).sum(axis=1).gt(1).sum()
    conflict_rate = conflicts / (L_train != ABSTAIN).any(axis=1).sum()
    coverage = (L_train != ABSTAIN).any(axis=1).mean()

    logger.info(f"Snorkel metrics: Coverage={coverage:.2f}, Conflict Rate={conflict_rate:.2f}")

    if coverage < COVERAGE_THRESHOLD or conflict_rate > 0.3: # Using a placeholder for abstain
         raise Exception(f"Snorkel quality did not meet threshold: Coverage={coverage}, Conflicts={conflict_rate}")

    # Step 4: Generate predictions and create silver labels df (Story 3.1/AC4, AC8)
    labels, probs = label_model.predict(L=L_train, return_probs=True)
    entropy = -np.sum(probs * np.log(probs + 1e-10), axis=1)

    df_silver_labels = v_features_asof.select(["symbol", "effective_date"]).with_columns(
        pl.Series("state_snorkel", labels),
        pl.Series("probability", np.max(probs, axis=1)),
        pl.Series("entropy", entropy),
    )

    # Auto-accept logic (AC8)
    df_silver_labels = df_silver_labels.with_columns(
        is_auto_accepted = pl.col("probability") >= AUTO_ACCEPT_THRESHOLD
    )

    # Add reproducibility info
    df_silver_labels = df_silver_labels.with_columns(
        label_model_version = pl.lit(label_model_version),
        lf_fingerprint = pl.lit(lf_fingerprint)
    )

    logger.info("Snorkel labeling process completed.")
    logger.info(f"Generated {df_silver_labels.height} silver labels.")

    upsert_silver_labels(conn, df_silver_labels)

    return df_silver_labels


def run_active_learning_sampling(conn, df_silver_labels: pl.DataFrame, v_features_asof: pl.DataFrame):
    """
    Selects uncertain and diverse samples for the Active Learning queue.
    (Implements Story 3.2)
    """
    logger.info("Starting Active Learning sampling process...")

    # Step 1: Filter candidates (Story 3.2/AC2)
    # Join with features to get HunterScore, FrothScore, etc.
    df_with_features = df_silver_labels.join(
        v_features_asof.select(["symbol", "effective_date", "HunterScore", "FrothScore", "sector", "cap_tercile"]),
        on=["symbol", "effective_date"],
        how="left"
    )

    # Filter out auto-accepted and identify candidates based on entropy or signal
    entropy_p90 = df_with_features.filter(pl.col("is_auto_accepted") == False)["entropy"].quantile(0.9)

    df_candidates = df_with_features.filter(
        (pl.col("is_auto_accepted") == False) &
        (
            (pl.col("entropy") >= entropy_p90) |
            ((pl.col("HunterScore") >= 80) & (pl.col("FrothScore") <= 20))
        )
    ).with_columns(
        reason = pl.when(pl.col("entropy") >= entropy_p90).then(pl.lit("entropy")).otherwise(pl.lit("hunter_score"))
    )

    if df_candidates.height == 0:
        logger.info("No candidates for Active Learning queue.")
        return

    logger.info(f"Found {df_candidates.height} candidates for AL queue.")

    # Step 2: Diversify (Story 3.2/AC3, AC5)
    # Stratified Round-Robin by adding a row number within each stratum
    df_diversified = df_candidates.with_columns(
        stratum_rank = pl.col("symbol").rank("ordinal", descending=False).over(["sector", "cap_tercile"])
    ).sort("stratum_rank")

    # Soft-cap per symbol per week
    df_diversified = df_diversified.with_columns(
        year_week = pl.col("effective_date").dt.strftime("%Y-%U"),
    ).with_columns(
        symbol_week_rank = pl.col("effective_date").rank("ordinal").over(["symbol", "year_week"])
    ).filter(pl.col("symbol_week_rank") <= 2)

    # Step 3: Apply Quota and add Honeypots (Story 3.2/AC7, AC9)
    df_selected = df_diversified.head(WEEKLY_AL_QUOTA)

    # TODO: Implement Honeypot injection logic (AC9)
    # For now, we just log that it's a placeholder.
    logger.info("Honeypot injection step placeholder.")

    # Step 4: Ghi vào Hàng đợi (Story 3.2/AC6)
    df_to_queue = df_selected.with_columns(
        dedup_key = pl.concat_str([
            pl.col("symbol"),
            pl.col("effective_date").cast(pl.Utf8),
            pl.col("year_week")
        ], separator=":"),
        status = pl.lit("pending")
    ).select(["symbol", "effective_date", "reason", "status", "dedup_key"])

    logger.info(f"Prepared {df_to_queue.height} samples for AL queue.")
    logger.info("Final samples to be queued:\n" + str(df_to_queue))

    insert_al_queue(conn, df_to_queue)

    logger.info("Active Learning sampling completed.")

def insert_al_queue(conn, df_to_queue: pl.DataFrame):
    """INSERT AL candidates into the database."""
    with conn.cursor() as cursor:
        cursor.execute("CREATE TEMP TABLE al_queue_temp (LIKE al_queue)")
        with cursor.copy("COPY al_queue_temp FROM STDIN") as copy:
            copy.write_polars_csv(df_to_queue)

        cursor.execute("""
            INSERT INTO al_queue (symbol, effective_date, reason, status, dedup_key)
            SELECT symbol, effective_date, reason, status, dedup_key FROM al_queue_temp
            ON CONFLICT (dedup_key) DO NOTHING
        """)
        logger.info(f"Inserted {cursor.rowcount} AL candidates.")


def main():
    """Main entry point for the labeling batch job."""
    ADVISORY_LOCK_NAME = "label_batch_lock"

    with get_db_connection() as conn:
        if not get_advisory_lock(conn, ADVISORY_LOCK_NAME):
            logger.warning(f"Could not acquire lock '{ADVISORY_LOCK_NAME}'. Exiting.")
            return 0

        try:
            logger.info("Starting label_batch job.")

            # Step 1: Fetch data from the as-of view (Story 3.1/AC6)
            logger.info("Fetching data from v_features_asof...")
            v_features_asof = pl.read_database("SELECT * FROM v_features_asof", conn)
            logger.info(f"Fetched {v_features_asof.height} rows from v_features_asof.")

            # Step 2: Run Snorkel to generate silver labels
            df_silver_labels = run_snorkel_labeling(conn, v_features_asof)

            # Step 3: Run AL sampling to populate the queue
            if df_silver_labels.height > 0:
                run_active_learning_sampling(conn, df_silver_labels, v_features_asof)
            else:
                logger.info("No silver labels generated, skipping AL sampling.")

            logger.info("label_batch job completed successfully.")
            conn.commit()

        except Exception as e:
            logger.error(f"An error occurred in the label_batch job: {e}", exc_info=True)
            if conn:
                conn.rollback()
            return 1
        finally:
            logger.info("Exiting label_batch job.")

if __name__ == "__main__":
    sys.exit(main())
