# src/worker/tasks_label.py

import os
import sys
import hashlib
import inspect
from contextlib import contextmanager

import numpy as np
import pandas as pd
import psycopg
from snorkel.labeling import LabelingFunction, PandasLFApplier
from snorkel.labeling.model import LabelModel
from scipy.stats import entropy

# Assuming core_lib is installed and accessible
from core_lib.db import pg_conn
from core_lib.locks import get_advisory_lock

# --- Labeling Constants ---
# These should be consistent with the HMM states if possible
ABSTAIN = -1
ACCUMULATION = 0
BURST = 1
DISTRIBUTION = 2
NEUTRAL = 3 # Or some other mapping

# --- Labeling Functions (LFs) ---

def LF_macro_tailwind(x):
    """Labels as ACCUMULATION if there's a strong macro tailwind."""
    if x.Macro_Impact_Score >= 5: # Tailwind bucket is >= +3, let's be more strict
        return ACCUMULATION
    return ABSTAIN

def LF_macro_shock(x):
    """Labels as DISTRIBUTION if a macro shock is active."""
    if x.macro_shock_flag == 1:
        return DISTRIBUTION
    return ABSTAIN

def LF_hmm_state(x):
    """Directly uses the state from the HMM as a vote."""
    # Assuming HMM states are already mapped: 0=Acc, 1=Burst, 2=Dist, 3=Neu
    state = int(x.state_hmm)
    if state in [ACCUMULATION, BURST, DISTRIBUTION, NEUTRAL]:
        return state
    return ABSTAIN

def LF_hunter_score_high(x):
    """Labels as ACCUMULATION for very high HunterScore."""
    if x.HunterScore >= 85:
        return ACCUMULATION
    return ABSTAIN

def LF_froth_score_high(x):
    """Labels as DISTRIBUTION for very high FrothScore."""
    if x.FrothScore >= 80:
        return DISTRIBUTION
    return ABSTAIN

def get_lfs():
    """Returns a list of all defined Labeling Functions."""
    return [
        LF_macro_tailwind,
        LF_macro_shock,
        LF_hmm_state,
        LF_hunter_score_high,
        LF_froth_score_high,
    ]

def get_lf_fingerprint(lfs):
    """Creates a fingerprint from the source code of the LFs."""
    source_code = "".join(inspect.getsource(lf) for lf in lfs)
    return hashlib.sha256(source_code.encode()).hexdigest()

def calculate_entropy(probs):
    """Calculates entropy for each row of predicted probabilities."""
    return np.apply_along_axis(entropy, 1, probs)

def run_label_batch():
    """
    Runs the Snorkel-based labeling pipeline.
    """
    lock_name = "label_batch"
    print(f"Attempting to acquire lock: {lock_name}")

    with pg_conn() as conn:
        if not get_advisory_lock(conn, lock_name):
            print(f"Could not acquire lock '{lock_name}'. Another process may be running. Exiting.")
            return 0

        print("Lock acquired. Starting label_batch.")

        try:
            with conn.cursor() as cur:
                # 1. Fetch data from v_features_asof (Story 3.1, AC6)
                print("Step 1: Fetching feature data for the last 2 years...")
                # Fetch a reasonable amount of data for training the label model
                query = """
                    SELECT * FROM v_features_asof
                    WHERE effective_date >= (NOW() - INTERVAL '2 year');
                """
                features_df = pd.read_sql(query, conn)
                print(f"Fetched {len(features_df)} records.")

                if features_df.empty:
                    print("No feature data found. Exiting.")
                    return 0

                # 2. Apply Labeling Functions (Story 3.1, AC2)
                print("Step 2: Applying labeling functions...")
                lfs = get_lfs()
                applier = PandasLFApplier(lfs=lfs)
                L_train = applier.apply(df=features_df)

                # 3. Train Snorkel Label Model (Story 3.1)
                print("Step 3: Training Snorkel LabelModel...")
                label_model = LabelModel(cardinality=4, verbose=False) # 4 classes: 0, 1, 2, 3
                label_model.fit(L_train=L_train, n_epochs=100, log_freq=20, seed=123)

                # 4. Guardrail: Check abstain rate (Story 3.1, AC5)
                print("Step 4: Checking quality guardrails...")
                preds, probs = label_model.predict(L=L_train, return_probs=True)

                abstain_mask = preds == ABSTAIN
                num_abstains = abstain_mask.sum()
                abstain_rate = num_abstains / len(preds)

                print(f"Snorkel model produced {num_abstains} abstains ({abstain_rate:.2%}).")
                if abstain_rate > 0.40:
                    raise ValueError(f"Abstain rate ({abstain_rate:.2f}) exceeds 40% threshold. Halting.")

                # 5. Prepare and save silver labels (Story 3.1, AC4)
                print("Step 5: Preparing and saving silver labels...")

                features_df['state_snorkel'] = preds
                features_df['probability'] = [p[pred] if pred != ABSTAIN else None for pred, p in zip(preds, probs)]
                features_df['entropy'] = calculate_entropy(probs)

                # Filter out abstains before saving
                labels_df = features_df[features_df['state_snorkel'] != ABSTAIN].copy()

                lf_fp = get_lf_fingerprint(lfs)
                labels_df['label_model_version'] = f"snorkel_v1_{lf_fp[:10]}"

                # Prepare for bulk upsert
                records_to_upsert = []
                for _, row in labels_df.iterrows():
                    records_to_upsert.append({
                        'symbol': row['symbol'],
                        'effective_date': row['effective_date'],
                        'state_hmm': int(row['state_hmm']) if pd.notna(row['state_hmm']) else None,
                        'state_snorkel': int(row['state_snorkel']),
                        'probability': float(row['probability']) if pd.notna(row['probability']) else None,
                        'entropy': float(row['entropy']) if pd.notna(row['entropy']) else None,
                        # 'source': json.dumps({'model': row['label_model_version']}), # JSONB field
                    })

                if records_to_upsert:
                    print(f"Upserting {len(records_to_upsert)} silver labels...")
                    # Using psycopg3's copy for efficiency would be even better
                    upsert_query = """
                        INSERT INTO labels_silver (symbol, effective_date, state_hmm, state_snorkel, probability, entropy)
                        VALUES (%(symbol)s, %(effective_date)s, %(state_hmm)s, %(state_snorkel)s, %(probability)s, %(entropy)s)
                        ON CONFLICT (symbol, effective_date) DO UPDATE SET
                            state_hmm = EXCLUDED.state_hmm,
                            state_snorkel = EXCLUDED.state_snorkel,
                            probability = EXCLUDED.probability,
                            entropy = EXCLUDED.entropy,
                            is_golden = FALSE; -- Reset golden flag on new silver label
                    """
                    cur.executemany(upsert_query, records_to_upsert, returning=False)
                    print(f"Upsert complete. {cur.rowcount} rows affected.")
                else:
                    print("No valid labels produced after filtering abstains.")

                # --- Story 3.2: Active Learning Sampling ---
                print("\n--- Starting Active Learning Sampling (Story 3.2) ---")

                # Use the full features_df which includes entropy scores for all samples
                al_candidates_df = features_df.copy()

                # AC2: Uncertainty & Prioritization Gate
                entropy_threshold = al_candidates_df['entropy'].quantile(0.90)
                is_uncertain = al_candidates_df['entropy'] >= entropy_threshold
                is_priority = (al_candidates_df['HunterScore'] >= 80) & (al_candidates_df['FrothScore'] <= 20)

                selected_for_al = al_candidates_df[is_uncertain | is_priority].copy()
                selected_for_al['reason'] = np.where(is_priority[selected_for_al.index], 'priority', 'entropy')
                print(f"Found {len(selected_for_al)} candidates based on uncertainty/priority.")

                # AC3, AC5: Diversity (Stratified Round-Robin and Symbol Cap)
                # Ensure date parts are available for weekly capping
                selected_for_al['yearweek'] = pd.to_datetime(selected_for_al['effective_date']).dt.to_period('W').astype(str)

                # Sort by priority then by uncertainty to select the best candidates first
                selected_for_al = selected_for_al.sort_values(by=['reason', 'entropy'], ascending=[False, False])

                final_selection = []
                symbol_weekly_counts = {} # To track (symbol, yearweek) counts

                # Assume 'sector' and 'cap_tercile' exist in features for stratification
                # If not, we'll just do a simple round-robin.
                strata_cols = []
                if 'sector' in selected_for_al.columns: strata_cols.append('sector')
                if 'cap_tercile' in selected_for_al.columns: strata_cols.append('cap_tercile')

                if strata_cols:
                    grouped = selected_for_al.groupby(strata_cols)
                    # Simple round-robin: iterate through groups and pick one at a time
                    max_len = max(len(group) for name, group in grouped)
                    for i in range(max_len):
                        for name, group in grouped:
                            if i < len(group):
                                sample = group.iloc[i]
                                key = (sample['symbol'], sample['yearweek'])
                                if symbol_weekly_counts.get(key, 0) < 2: # AC5: Soft-cap
                                    final_selection.append(sample)
                                    symbol_weekly_counts[key] = symbol_weekly_counts.get(key, 0) + 1
                else: # Fallback if strata columns are missing
                    for _, sample in selected_for_al.iterrows():
                        key = (sample['symbol'], sample['yearweek'])
                        if symbol_weekly_counts.get(key, 0) < 2:
                            final_selection.append(sample)
                            symbol_weekly_counts[key] = symbol_weekly_counts.get(key, 0) + 1

                print(f"Selected {len(final_selection)} diverse samples.")

                # AC9: Add 1-2 Honeypots
                honeypots_df = labels_df[labels_df['probability'] > 0.98].head(2)
                for _, honeypot in honeypots_df.iterrows():
                    final_selection.append(honeypot)

                print(f"Added {len(honeypots_df)} honeypots.")

                # AC7: Respect the weekly quota
                AL_WEEKLY_QUOTA = 10
                final_samples_df = pd.DataFrame(final_selection).head(AL_WEEKLY_QUOTA)

                # AC6: Write to AL Queue
                if not final_samples_df.empty:
                    al_records = []
                    for _, row in final_samples_df.iterrows():
                        # Ensure yearweek is calculated if missing (for honeypots)
                        if 'yearweek' not in row or pd.isna(row['yearweek']):
                            yearweek_val = pd.to_datetime(row['effective_date']).dt.to_period('W').astype(str)
                        else:
                            yearweek_val = row['yearweek']

                        al_records.append({
                            'symbol': row['symbol'],
                            'effective_date': row['effective_date'],
                            'reason': row.get('reason', 'honeypot'),
                            'status': 'pending',
                            'dedup_key': f"{row['symbol']}:{row['effective_date'].strftime('%Y-%m-%d')}:{yearweek_val}"
                        })

                    print(f"Final AL selection count: {len(al_records)}. Pushing to al_queue...")
                    al_upsert_query = """
                        INSERT INTO al_queue (symbol, effective_date, reason, status, dedup_key)
                        VALUES (%(symbol)s, %(effective_date)s, %(reason)s, %(status)s, %(dedup_key)s)
                        ON CONFLICT (dedup_key) DO NOTHING;
                    """
                    cur.executemany(al_upsert_query, al_records, returning=False)
                    print(f"AL queue upsert complete. {cur.rowcount} new samples added.")

                print("---------------------------------------------------\n")

            conn.commit()
            print("label_batch (Story 3.1) completed successfully and transaction committed.")

        except psycopg.Error as e:
            print(f"Database error during label_batch: {e}", file=sys.stderr)
            conn.rollback()
            raise
        except Exception as e:
            print(f"General error during label_batch: {e}", file=sys.stderr)
            conn.rollback()
            raise

    # The advisory lock is released automatically by the DB session ending.
    print(f"Lock '{lock_name}' released.")
    return 0


if __name__ == "__main__":
    try:
        run_label_batch()
        sys.exit(0)
    except Exception as e:
        # The error is already printed, just exit with a failure code
        sys.exit(1)
