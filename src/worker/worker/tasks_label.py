"""
Snorkel Weak Supervision - Stock Hunter AI
===========================================
Batch job to generate probabilistic labels using labeling functions (LFs).

Story 3.1: Runs daily at 02:40, applies LFs, trains Snorkel Label Model,
          outputs to labels_silver with quality guardrails.

Story 3.2: Generates Active Learning queue (al_queue) for high-uncertainty samples.

Reference: PRD V2.7.2 Epic 3
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict
import numpy as np
from collections import Counter

from core_lib.db import get_db_connection

# --- Constants ---
ADVISORY_LOCK_NAME = "label_batch"
LABEL_MODEL_VERSION = os.getenv("LABEL_MODEL_VERSION", "v1.0")

# Quality Guardrails (AC5)
MAX_ABSTAIN_RATE = 0.40  # 40% max
MIN_COVERAGE = 0.60      # 60% min

# Auto-Accept Threshold (AC8)
AUTO_ACCEPT_CONFIDENCE = 0.90  # 90% confidence

# Active Learning (Story 3.2)
WEEKLY_AL_QUOTA = 10  # Max 10 samples per week
MAX_SAMPLES_PER_SYMBOL_PER_WEEK = 2

# Label Classes
LABEL_ABSTAIN = -1
LABEL_NO_BUY = 0
LABEL_BUY = 1

# --- Labeling Functions (LFs) - AC2 ---

def LF_hmm_accumulation(features: Dict) -> int:
    """
    LF based on HMM state.
    Logic: If in Accumulation (state 0), likely a good setup (BUY).
    """
    hmm_state = features.get('hmm_state')

    if hmm_state is None:
        return LABEL_ABSTAIN

    if hmm_state == 0:  # Accumulation
        return LABEL_BUY
    elif hmm_state == 2:  # Euphoria
        return LABEL_NO_BUY
    else:
        return LABEL_ABSTAIN

def LF_hmm_breakout(features: Dict) -> int:
    """
    LF based on HMM Breakout state.
    Logic: If in Breakout (state 1), vote BUY.
    """
    hmm_state = features.get('hmm_state')

    if hmm_state == 1:  # Breakout
        return LABEL_BUY
    else:
        return LABEL_ABSTAIN

def LF_hunter_high_froth_low(features: Dict) -> int:
    """
    LF based on HunterScore/FrothScore combination.
    Logic: HunterScore >= 80 AND FrothScore <= 20 => BUY (strong signal, low noise)
    """
    hunter_score = features.get('hunter_score')
    froth_score = features.get('froth_score')

    if hunter_score is None or froth_score is None:
        return LABEL_ABSTAIN

    if hunter_score >= 80 and froth_score <= 20:
        return LABEL_BUY
    elif hunter_score < 40 or froth_score > 70:
        return LABEL_NO_BUY
    else:
        return LABEL_ABSTAIN

def LF_froth_extreme(features: Dict) -> int:
    """
    LF based on extreme FrothScore.
    Logic: FrothScore > 80 => NO_BUY (extreme froth, danger zone)
    """
    froth_score = features.get('froth_score')

    if froth_score is None:
        return LABEL_ABSTAIN

    if froth_score > 80:
        return LABEL_NO_BUY
    else:
        return LABEL_ABSTAIN

def LF_macro_tailwind(features: Dict) -> int:
    """
    LF based on Macro_Impact_Score.
    Logic: Macro >= +3 (Tailwind) => BUY bias
           Macro <= -3 (Headwind) => NO_BUY bias
    """
    macro_impact = features.get('macro_impact_score')

    if macro_impact is None:
        return LABEL_ABSTAIN

    if macro_impact >= 3:
        return LABEL_BUY
    elif macro_impact <= -3:
        return LABEL_NO_BUY
    else:
        return LABEL_ABSTAIN

def LF_hunter_low_only(features: Dict) -> int:
    """
    LF based on very low HunterScore.
    Logic: HunterScore < 20 => NO_BUY (weak signal)
    """
    hunter_score = features.get('hunter_score')

    if hunter_score is None:
        return LABEL_ABSTAIN

    if hunter_score < 20:
        return LABEL_NO_BUY
    else:
        return LABEL_ABSTAIN

# All LFs registry
LABELING_FUNCTIONS = [
    LF_hmm_accumulation,
    LF_hmm_breakout,
    LF_hunter_high_froth_low,
    LF_froth_extreme,
    LF_macro_tailwind,
    LF_hunter_low_only,
]

# --- LF Fingerprint (AC3 - Reproducibility) ---

def compute_lf_fingerprint() -> str:
    """
    Computes hash of all LF source code for reproducibility.
    Changes to LFs will change the fingerprint, triggering retraining.
    """
    import inspect

    lf_sources = []
    for lf in LABELING_FUNCTIONS:
        source = inspect.getsource(lf)
        lf_sources.append(source)

    combined = "\n".join(lf_sources)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]

# --- Snorkel Label Model (Simplified Majority Vote) ---

class MajorityVoteLabelModel:
    """
    Simplified label model using weighted majority vote.

    In production, use Snorkel's actual Label Model with generative model.
    For this implementation, we use majority vote with LF accuracy weights.
    """

    def __init__(self):
        self.lf_weights = None

    def fit(self, L: np.ndarray):
        """
        Estimate LF accuracies based on agreement patterns.

        Args:
            L: Label matrix (n_samples x n_lfs)
        """
        n_samples, n_lfs = L.shape

        # Simple heuristic: LF weight based on agreement with other LFs
        agreements = np.zeros(n_lfs)

        for i in range(n_lfs):
            for j in range(n_lfs):
                if i != j:
                    # Count agreements (ignoring abstains)
                    mask = (L[:, i] != LABEL_ABSTAIN) & (L[:, j] != LABEL_ABSTAIN)
                    if np.sum(mask) > 0:
                        agreements[i] += np.sum(L[mask, i] == L[mask, j]) / np.sum(mask)

        # Normalize to weights (avoid division by zero)
        self.lf_weights = agreements / (agreements.sum() + 1e-6)

        # Ensure minimum weight
        self.lf_weights = np.maximum(self.lf_weights, 0.1)
        self.lf_weights /= self.lf_weights.sum()

    def predict_proba(self, L: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Predict probabilistic labels.

        Args:
            L: Label matrix (n_samples x n_lfs)

        Returns:
            predicted_labels: Most likely label (0 or 1, or -1 if abstain)
            probabilities: Probability of predicted label
            entropies: Entropy of prediction (uncertainty measure)
        """
        if self.lf_weights is None:
            raise ValueError("Model not fitted. Call fit() first.")

        n_samples = L.shape[0]
        predicted_labels = np.full(n_samples, LABEL_ABSTAIN, dtype=int)
        probabilities = np.zeros(n_samples)
        entropies = np.zeros(n_samples)

        for i in range(n_samples):
            votes = L[i, :]
            valid_mask = votes != LABEL_ABSTAIN

            if not np.any(valid_mask):
                # All LFs abstained
                predicted_labels[i] = LABEL_ABSTAIN
                probabilities[i] = 0.0
                entropies[i] = 0.0  # Max entropy
                continue

            # Weighted vote
            valid_votes = votes[valid_mask]
            valid_weights = self.lf_weights[valid_mask]

            # Normalize weights
            valid_weights /= valid_weights.sum()

            # Count votes for each class
            vote_buy = np.sum(valid_weights[valid_votes == LABEL_BUY])
            vote_no_buy = np.sum(valid_weights[valid_votes == LABEL_NO_BUY])

            # Predict
            if vote_buy > vote_no_buy:
                predicted_labels[i] = LABEL_BUY
                probabilities[i] = vote_buy / (vote_buy + vote_no_buy)
            else:
                predicted_labels[i] = LABEL_NO_BUY
                probabilities[i] = vote_no_buy / (vote_buy + vote_no_buy)

            # Entropy: measure of uncertainty
            # H = -p*log(p) - (1-p)*log(1-p)
            p = probabilities[i]
            if p > 0 and p < 1:
                entropies[i] = -p * np.log2(p) - (1 - p) * np.log2(1 - p)
            else:
                entropies[i] = 0.0

        return predicted_labels, probabilities, entropies

# --- Feature Fetching ---

def fetch_features_for_labeling(conn, as_of_date: str) -> List[Dict]:
    """
    Fetches features from features_gold for all symbols on a specific date.

    Returns list of dicts with:
    - symbol
    - effective_date
    - hmm_state
    - hunter_score
    - froth_score
    - macro_impact_score
    """
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT
                symbol,
                effective_date,
                MAX(CASE WHEN feature_name = 'hmm_state' THEN value END) as hmm_state,
                MAX(CASE WHEN feature_name = 'hunter_score' THEN value END) as hunter_score,
                MAX(CASE WHEN feature_name = 'froth_score' THEN value END) as froth_score,
                MAX(CASE WHEN feature_name = 'macro_impact_score' THEN value END) as macro_impact_score
            FROM features_gold
            WHERE effective_date = %s
            GROUP BY symbol, effective_date
            HAVING COUNT(*) >= 4  -- Ensure all features present
        """, (as_of_date,))

        rows = cursor.fetchall()

    features_list = []
    for row in rows:
        features_list.append({
            'symbol': row[0],
            'effective_date': row[1],
            'hmm_state': int(row[2]) if row[2] is not None else None,
            'hunter_score': float(row[3]) if row[3] is not None else None,
            'froth_score': float(row[4]) if row[4] is not None else None,
            'macro_impact_score': int(row[5]) if row[5] is not None else None,
        })

    return features_list

# --- Apply LFs ---

def apply_labeling_functions(features_list: List[Dict]) -> np.ndarray:
    """
    Applies all LFs to features and returns label matrix L.

    Args:
        features_list: List of feature dicts

    Returns:
        L: Label matrix (n_samples x n_lfs), values in {-1, 0, 1}
    """
    n_samples = len(features_list)
    n_lfs = len(LABELING_FUNCTIONS)

    L = np.full((n_samples, n_lfs), LABEL_ABSTAIN, dtype=int)

    for i, features in enumerate(features_list):
        for j, lf in enumerate(LABELING_FUNCTIONS):
            L[i, j] = lf(features)

    return L

# --- Quality Guardrails (AC5) ---

def check_quality_guardrails(L: np.ndarray) -> Tuple[bool, str]:
    """
    Checks if label matrix passes quality guardrails.

    Returns:
        (passed, message)
    """
    n_samples, n_lfs = L.shape

    # Abstain rate: % of samples where ALL LFs abstained
    all_abstain = np.all(L == LABEL_ABSTAIN, axis=1)
    abstain_rate = np.sum(all_abstain) / n_samples

    # Coverage: % of samples with at least one non-abstain vote
    coverage = 1.0 - abstain_rate

    if abstain_rate > MAX_ABSTAIN_RATE:
        return False, f"Abstain rate {abstain_rate:.2%} > {MAX_ABSTAIN_RATE:.2%}"

    if coverage < MIN_COVERAGE:
        return False, f"Coverage {coverage:.2%} < {MIN_COVERAGE:.2%}"

    return True, f"Quality OK: abstain_rate={abstain_rate:.2%}, coverage={coverage:.2%}"

# --- Active Learning Queue Generation (Story 3.2) ---

def generate_al_queue(conn, labels_data: List[Dict]):
    """
    Generates Active Learning queue for high-value samples.

    Criteria (AC2):
    - High entropy (uncertain predictions)
    - OR strong signals (HunterScore >= 80 AND FrothScore <= 20)

    Diversification (AC3, AC5):
    - Max 2 samples per symbol per week
    - Stratified by sector and cap_tercile (if available)

    Quota (AC7):
    - Max WEEKLY_AL_QUOTA samples per week
    """

    # Filter candidates
    candidates = []

    for label_data in labels_data:
        # Skip auto-accepted labels
        if label_data['probability'] >= AUTO_ACCEPT_CONFIDENCE:
            continue

        # High entropy (uncertain)
        is_uncertain = label_data['entropy'] > 0.7

        # Strong signal (high value for review)
        is_strong_signal = (
            label_data.get('hunter_score', 0) >= 80 and
            label_data.get('froth_score', 100) <= 20
        )

        if is_uncertain or is_strong_signal:
            candidates.append(label_data)

    # Check current week's quota
    with conn.cursor() as cursor:
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime('%Y-%m-%d')

        cursor.execute("""
            SELECT COUNT(*) FROM al_queue
            WHERE created_at >= %s
              AND status = 'pending'
        """, (week_start,))

        current_count = cursor.fetchone()[0]

        if current_count >= WEEKLY_AL_QUOTA:
            print(f"AL queue quota reached: {current_count}/{WEEKLY_AL_QUOTA} for this week")
            return

        # Check per-symbol quota
        cursor.execute("""
            SELECT symbol, COUNT(*) as count
            FROM al_queue
            WHERE created_at >= %s
              AND status = 'pending'
            GROUP BY symbol
        """, (week_start,))

        symbol_counts = {row[0]: row[1] for row in cursor.fetchall()}

    # Diversification: Sort by entropy (descending) and limit per symbol
    candidates_sorted = sorted(candidates, key=lambda x: x['entropy'], reverse=True)

    selected = []
    for candidate in candidates_sorted:
        if len(selected) >= (WEEKLY_AL_QUOTA - current_count):
            break

        symbol = candidate['symbol']

        # Check per-symbol limit
        if symbol_counts.get(symbol, 0) >= MAX_SAMPLES_PER_SYMBOL_PER_WEEK:
            continue

        selected.append(candidate)
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1

    # Insert into al_queue
    with conn.cursor() as cursor:
        for item in selected:
            dedup_key = f"{item['symbol']}:{item['effective_date']}"

            cursor.execute("""
                INSERT INTO al_queue (symbol, effective_date, reason, status, dedup_key)
                VALUES (%s, %s, %s, 'pending', %s)
                ON CONFLICT (dedup_key) DO NOTHING
            """, (
                item['symbol'],
                item['effective_date'],
                f"entropy={item['entropy']:.3f}, hunter={item.get('hunter_score', 0):.0f}",
                dedup_key
            ))

    print(f"Added {len(selected)} samples to AL queue")

# --- Main Label Batch ---

def run_label_batch():
    """
    Main entrypoint for label_batch job.

    Execution flow:
    1. Acquire advisory lock
    2. Fetch features from features_gold
    3. Apply LFs to generate label matrix L
    4. Train Snorkel Label Model
    5. Predict probabilistic labels
    6. Check quality guardrails
    7. Write to labels_silver
    8. Generate AL queue
    9. Release lock
    """

    print("=" * 60)
    print("Label Batch (Snorkel Weak Supervision) starting...")
    print(f"Label Model Version: {LABEL_MODEL_VERSION}")
    print("=" * 60)

    conn = None

    try:
        conn = get_db_connection()

        # AC1: Acquire advisory lock
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (ADVISORY_LOCK_NAME,))
            acquired = cursor.fetchone()[0]

            if not acquired:
                print(f"Lock '{ADVISORY_LOCK_NAME}' already held. Exiting.")
                return 0

        print(f" Acquired lock '{ADVISORY_LOCK_NAME}'")

        # Determine as_of_date (previous day - assume features_gold ran yesterday)
        as_of_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        # Step 1: Fetch features
        print(f"\nFetching features for {as_of_date}...")
        features_list = fetch_features_for_labeling(conn, as_of_date)
        print(f" Fetched {len(features_list)} samples with features")

        if len(features_list) == 0:
            print("No features found. Exiting.")
            return 0

        # Step 2: Apply LFs (AC2)
        print(f"\nApplying {len(LABELING_FUNCTIONS)} labeling functions...")
        L = apply_labeling_functions(features_list)

        # Compute LF fingerprint (AC3)
        lf_fingerprint = compute_lf_fingerprint()
        print(f" LF Fingerprint: {lf_fingerprint}")

        # Step 3: Check quality guardrails (AC5)
        passed, message = check_quality_guardrails(L)
        print(f"\nQuality Guardrails: {message}")

        if not passed:
            print(f"ERROR: Quality guardrails failed. Aborting.")
            return 1

        # Step 4: Train Label Model
        print("\nTraining Snorkel Label Model...")
        model = MajorityVoteLabelModel()
        model.fit(L)
        print(f" Model trained. LF weights: {model.lf_weights}")

        # Step 5: Predict
        print("\nGenerating probabilistic labels...")
        predicted_labels, probabilities, entropies = model.predict_proba(L)

        # Combine with features
        labels_data = []
        auto_accept_count = 0

        for i, features in enumerate(features_list):
            prob = probabilities[i]
            entropy = entropies[i]

            # Auto-accept (AC8)
            is_golden = prob >= AUTO_ACCEPT_CONFIDENCE

            if is_golden:
                auto_accept_count += 1

            labels_data.append({
                **features,
                'state_snorkel': int(predicted_labels[i]),
                'probability': float(prob),
                'entropy': float(entropy),
                'is_golden': is_golden,
                'label_model_version': LABEL_MODEL_VERSION,
                'lf_fingerprint': lf_fingerprint,
            })

        print(f" Generated {len(labels_data)} labels")
        print(f"  - Auto-accepted (confidence >= {AUTO_ACCEPT_CONFIDENCE}): {auto_accept_count}")
        print(f"  - Needs review: {len(labels_data) - auto_accept_count}")

        # Step 6: Write to labels_silver (AC4)
        print("\nWriting to labels_silver...")
        with conn.cursor() as cursor:
            for label_data in labels_data:
                # Convert source to JSON (LF votes)
                lf_votes = L[features_list.index(next(f for f in features_list if f['symbol'] == label_data['symbol']))].tolist()

                cursor.execute("""
                    INSERT INTO labels_silver (
                        symbol, effective_date, state_hmm, state_snorkel,
                        probability, entropy, source, is_golden
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                    )
                    ON CONFLICT (symbol, effective_date)
                    DO UPDATE SET
                        state_snorkel = EXCLUDED.state_snorkel,
                        probability = EXCLUDED.probability,
                        entropy = EXCLUDED.entropy,
                        source = EXCLUDED.source,
                        is_golden = EXCLUDED.is_golden
                """, (
                    label_data['symbol'],
                    label_data['effective_date'],
                    label_data.get('hmm_state'),
                    label_data['state_snorkel'],
                    label_data['probability'],
                    label_data['entropy'],
                    json.dumps({'lf_votes': lf_votes, 'lf_fingerprint': lf_fingerprint}),
                    label_data['is_golden']
                ))

        conn.commit()
        print(f" Wrote {len(labels_data)} labels to labels_silver")

        # Step 7: Generate AL queue (Story 3.2)
        print("\nGenerating Active Learning queue...")
        generate_al_queue(conn, labels_data)

        # Release lock
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (ADVISORY_LOCK_NAME,))

        print("=" * 60)
        print("Label Batch finished successfully!")
        print("=" * 60)

        return 0

    except Exception as e:
        print(f"ERROR: Label Batch failed: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
        return 1

    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    import sys
    sys.exit(run_label_batch())
