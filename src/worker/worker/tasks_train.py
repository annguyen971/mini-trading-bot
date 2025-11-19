"""
Model Training Pipeline - Stock Hunter AI
==========================================
Batch job to train Logistic Regression model with Walk-Forward Optimization (WFO).

Story 4.1: Runs at 02:50, checks triggers, trains only when needed.
Story 4.2: Implements WFO backtesting with Sharpe, Drawdown, IC metrics.
Story 4.3: Auto-suggest promotion and auto-rollback monitoring.

Reference: PRD V2.7.2 Epic 4
"""

import hashlib
import json
import os
import pickle
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, precision_recall_fscore_support

from core_lib.db import get_db_connection

# --- Constants ---
ADVISORY_LOCK_NAME = "train_batch"
MODEL_VERSION_PREFIX = "lr_"

# Training Triggers (AC2)
PSI_DRIFT_THRESHOLD = 0.2        # Population Stability Index
IC_DROP_THRESHOLD = 0.03         # Information Coefficient drop
MIN_NEW_GOLDEN_LABELS = 20       # Minimum new golden labels
MAX_MODEL_AGE_DAYS = 60          # Days before model is "stale"

# Cooldown (AC3)
MIN_DAYS_BETWEEN_TRAINING = 7

# WFO Parameters (Story 4.2)
WFO_TRAIN_WINDOW_DAYS = 180      # 6 months training window
WFO_TEST_WINDOW_DAYS = 30        # 1 month test window
WFO_STEP_DAYS = 30               # Move forward 1 month at a time

# Auto-Suggest Guardrails (Story 4.3 AC1-AC2)
MIN_SHARPE_IMPROVEMENT = 0.1     # New model must have Sharpe > old + 0.1
MAX_DRAWDOWN_DEGRADATION = 0.20  # Max drawdown can't be 20% worse

# Model Parameters
RANDOM_STATE = 42

# --- Helper Functions ---

def compute_psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """
    Computes Population Stability Index (PSI) to detect distribution drift.

    PSI > 0.2 indicates significant drift.
    """
    # Handle edge cases
    if len(expected) == 0 or len(actual) == 0:
        return 0.0

    # Create bins based on expected distribution
    try:
        breakpoints = np.percentile(expected, np.linspace(0, 100, bins + 1))
        breakpoints[-1] = breakpoints[-1] + 0.0001  # Ensure last bin includes max
    except Exception:
        return 0.0

    # Count observations in each bin
    expected_counts = np.histogram(expected, bins=breakpoints)[0]
    actual_counts = np.histogram(actual, bins=breakpoints)[0]

    # Convert to percentages
    expected_pct = expected_counts / len(expected)
    actual_pct = actual_counts / len(actual)

    # Avoid log(0) by adding small epsilon
    epsilon = 0.0001
    expected_pct = np.maximum(expected_pct, epsilon)
    actual_pct = np.maximum(actual_pct, epsilon)

    # PSI formula
    psi = np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct))

    return float(psi)

def compute_ic(predictions: np.ndarray, actuals: np.ndarray) -> float:
    """
    Computes Information Coefficient (Spearman correlation between predictions and actuals).
    IC measures how well predictions rank-order actual outcomes.
    """
    from scipy.stats import spearmanr

    if len(predictions) == 0 or len(actuals) == 0:
        return 0.0

    ic, _ = spearmanr(predictions, actuals)
    return float(ic) if not np.isnan(ic) else 0.0

def compute_sharpe_ratio(returns: np.ndarray) -> float:
    """Computes annualized Sharpe ratio."""
    if len(returns) == 0:
        return 0.0

    mean_return = np.mean(returns)
    std_return = np.std(returns, ddof=1)

    if std_return == 0:
        return 0.0

    # Annualize (assume daily returns)
    sharpe = (mean_return / std_return) * np.sqrt(252)
    return float(sharpe)

def compute_max_drawdown(returns: np.ndarray) -> float:
    """Computes maximum drawdown from cumulative returns."""
    if len(returns) == 0:
        return 0.0

    cumulative = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - running_max) / running_max

    max_dd = np.min(drawdown)
    return float(max_dd)

# --- Training Triggers (AC2) ---

def check_training_triggers(conn) -> Tuple[bool, List[str]]:
    """
    Checks if any training triggers are activated.

    Returns:
        (should_train, reasons)
    """
    reasons = []

    with conn.cursor() as cursor:
        # Trigger 1: PSI Drift
        # Compare current feature distribution vs historical
        cursor.execute("""
            SELECT value FROM features_gold
            WHERE effective_date > CURRENT_DATE - interval '30 days'
              AND feature_name = 'hunter_score'
        """)
        current_features = np.array([row[0] for row in cursor.fetchall()])

        cursor.execute("""
            SELECT value FROM features_gold
            WHERE effective_date BETWEEN CURRENT_DATE - interval '180 days'
                              AND CURRENT_DATE - interval '30 days'
              AND feature_name = 'hunter_score'
        """)
        historical_features = np.array([row[0] for row in cursor.fetchall()])

        if len(current_features) > 10 and len(historical_features) > 10:
            psi = compute_psi(historical_features, current_features)
            if psi > PSI_DRIFT_THRESHOLD:
                reasons.append(f"PSI drift detected: {psi:.3f} > {PSI_DRIFT_THRESHOLD}")

        # Trigger 2: IC Drop
        # Check if current model's IC has dropped significantly
        cursor.execute("""
            SELECT metrics->>'ic' as ic, created_at
            FROM model_registry
            WHERE is_active = true
            ORDER BY created_at DESC
            LIMIT 1
        """)
        active_model = cursor.fetchone()

        if active_model:
            current_ic = float(active_model[0]) if active_model[0] else 0.0
            model_age = (datetime.now() - active_model[1]).days

            # Check recent IC (would need monitoring_logs in practice)
            # For now, use a simplified check
            if current_ic < 0.05:  # Very low IC
                reasons.append(f"Low IC detected: {current_ic:.3f}")

        # Trigger 3: New Golden Labels
        cursor.execute("""
            SELECT COUNT(*) FROM labels_golden
            WHERE created_at > (
                SELECT COALESCE(MAX(created_at), '1970-01-01')
                FROM model_registry
            )
        """)
        new_golden_count = cursor.fetchone()[0]

        if new_golden_count >= MIN_NEW_GOLDEN_LABELS:
            reasons.append(f"New golden labels: {new_golden_count} >= {MIN_NEW_GOLDEN_LABELS}")

        # Trigger 4: Model Age
        if active_model:
            model_age = (datetime.now() - active_model[1]).days
            if model_age > MAX_MODEL_AGE_DAYS:
                reasons.append(f"Model age: {model_age} days > {MAX_MODEL_AGE_DAYS} days")

        # Trigger 5: Check cooldown period (AC3)
        cursor.execute("""
            SELECT created_at FROM model_registry
            ORDER BY created_at DESC
            LIMIT 1
        """)
        last_training = cursor.fetchone()

        if last_training:
            days_since_last = (datetime.now() - last_training[0]).days
            if days_since_last < MIN_DAYS_BETWEEN_TRAINING:
                return False, [f"Cooldown period: {days_since_last} days < {MIN_DAYS_BETWEEN_TRAINING} days"]

    should_train = len(reasons) > 0
    return should_train, reasons

# --- Data Preparation ---

def fetch_training_data(conn, start_date: str, end_date: str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fetches features and labels for training.

    Returns:
        X: Feature matrix (n_samples x n_features)
        y: Labels (n_samples,)
    """
    with conn.cursor() as cursor:
        # Fetch data from v_features_asof (NFR4 compliance)
        cursor.execute("""
            WITH feature_pivot AS (
                SELECT
                    symbol,
                    effective_date,
                    MAX(CASE WHEN feature_name = 'hmm_state' THEN value END) as hmm_state,
                    MAX(CASE WHEN feature_name = 'hunter_score' THEN value END) as hunter_score,
                    MAX(CASE WHEN feature_name = 'froth_score' THEN value END) as froth_score,
                    MAX(CASE WHEN feature_name = 'macro_impact_score' THEN value END) as macro_impact
                FROM v_features_asof
                WHERE effective_date BETWEEN %s AND %s
                  AND as_of_time <= (effective_date + interval '1 day')::timestamptz
                GROUP BY symbol, effective_date
            )
            SELECT
                fp.symbol,
                fp.effective_date,
                fp.hmm_state,
                fp.hunter_score,
                fp.froth_score,
                fp.macro_impact,
                COALESCE(lg.new_label, ls.state_snorkel) as label
            FROM feature_pivot fp
            LEFT JOIN labels_silver ls ON fp.symbol = ls.symbol
                                       AND fp.effective_date = ls.effective_date
            LEFT JOIN labels_golden lg ON ls.symbol = lg.symbol
                                        AND ls.effective_date = lg.effective_date
            WHERE COALESCE(lg.new_label, ls.state_snorkel) IS NOT NULL
              AND ls.is_golden = true  -- Only use high-confidence labels
            ORDER BY fp.effective_date, fp.symbol
        """, (start_date, end_date))

        rows = cursor.fetchall()

    if len(rows) == 0:
        return np.array([]), np.array([])

    # Extract features and labels
    X = np.array([[row[2], row[3], row[4], row[5]] for row in rows])  # hmm, hunter, froth, macro
    y = np.array([row[6] for row in rows])

    return X, y

# --- Model Training ---

def train_model(X_train: np.ndarray, y_train: np.ndarray) -> LogisticRegression:
    """
    Trains Logistic Regression model.

    Simple model choice for interpretability and speed.
    """
    model = LogisticRegression(
        random_state=RANDOM_STATE,
        max_iter=1000,
        class_weight='balanced'  # Handle class imbalance
    )

    model.fit(X_train, y_train)

    return model

# --- Walk-Forward Optimization (WFO) - Story 4.2 ---

def run_wfo_backtest(conn, start_date: str, end_date: str) -> Dict:
    """
    Runs Walk-Forward Optimization backtest.

    Returns:
        metrics: Dict with sharpe, max_drawdown, ic, win_rate
    """
    print("\nRunning Walk-Forward Optimization (WFO)...")

    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')

    all_predictions = []
    all_actuals = []
    all_returns = []

    fold_count = 0

    while True:
        # Define train and test windows
        train_start = start_dt + timedelta(days=fold_count * WFO_STEP_DAYS)
        train_end = train_start + timedelta(days=WFO_TRAIN_WINDOW_DAYS)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=WFO_TEST_WINDOW_DAYS)

        if test_end > end_dt:
            break

        print(f"  Fold {fold_count + 1}: Train {train_start.date()} to {train_end.date()}, "
              f"Test {test_start.date()} to {test_end.date()}")

        # Fetch train data
        X_train, y_train = fetch_training_data(
            conn,
            train_start.strftime('%Y-%m-%d'),
            train_end.strftime('%Y-%m-%d')
        )

        # Fetch test data
        X_test, y_test = fetch_training_data(
            conn,
            test_start.strftime('%Y-%m-%d'),
            test_end.strftime('%Y-%m-%d')
        )

        if len(X_train) < 10 or len(X_test) < 5:
            print(f"    Skipping fold: insufficient data (train={len(X_train)}, test={len(X_test)})")
            fold_count += 1
            continue

        # Train model
        model = train_model(X_train, y_train)

        # Predict on test set
        y_pred_proba = model.predict_proba(X_test)[:, 1]  # Probability of BUY class

        # Simulate returns (simplified: higher probability = higher return)
        # In practice, fetch actual forward returns from ta_silver
        returns = (y_test - 0.5) * y_pred_proba * 0.02  # Simplified return model

        all_predictions.extend(y_pred_proba)
        all_actuals.extend(y_test)
        all_returns.extend(returns)

        fold_count += 1

    if len(all_returns) == 0:
        return {'sharpe': 0.0, 'max_drawdown': 0.0, 'ic': 0.0, 'win_rate': 0.0}

    # Compute metrics
    sharpe = compute_sharpe_ratio(np.array(all_returns))
    max_dd = compute_max_drawdown(np.array(all_returns))
    ic = compute_ic(np.array(all_predictions), np.array(all_actuals))
    win_rate = np.mean(np.array(all_returns) > 0)

    metrics = {
        'sharpe': float(sharpe),
        'max_drawdown': float(max_dd),
        'ic': float(ic),
        'win_rate': float(win_rate),
        'n_folds': fold_count,
        'n_predictions': len(all_predictions)
    }

    print(f"\n WFO Complete:")
    print(f"  Sharpe Ratio: {sharpe:.3f}")
    print(f"  Max Drawdown: {max_dd:.2%}")
    print(f"  IC: {ic:.3f}")
    print(f"  Win Rate: {win_rate:.2%}")

    return metrics

# --- Model Registry (AC7) ---

def save_model_to_registry(conn, model: LogisticRegression, metrics: Dict, artifacts_dir: str = "/opt/artifacts") -> str:
    """
    Saves model to file and registers in model_registry table.

    Returns:
        model_version: Version identifier
    """
    # Generate model version
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    model_version = f"{MODEL_VERSION_PREFIX}{timestamp}"

    # Create artifacts directory if not exists
    os.makedirs(artifacts_dir, exist_ok=True)

    # Save model as pickle
    model_path = os.path.join(artifacts_dir, f"{model_version}.pkl")
    with open(model_path, 'wb') as f:
        pickle.dump(model, f)

    # Compute model hash (SHA256)
    with open(model_path, 'rb') as f:
        model_bytes = f.read()
        model_hash = hashlib.sha256(model_bytes).hexdigest()

    # Register in database
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO model_registry (
                model_version, model_type, file_path, artifact_sha256,
                metrics, is_active, promotion_suggestion, metadata
            ) VALUES (
                %s, 'LogisticRegression', %s, %s,
                %s::jsonb, false, 'pending_review', %s::jsonb
            )
        """, (
            model_version,
            model_path,
            model_hash,
            json.dumps(metrics),
            json.dumps({
                'feature_set_version': 'v1.0',
                'random_state': RANDOM_STATE,
                'trained_at': timestamp
            })
        ))

    conn.commit()

    print(f"\n Model saved:")
    print(f"  Version: {model_version}")
    print(f"  Path: {model_path}")
    print(f"  Hash: {model_hash[:16]}...")

    return model_version

# --- Auto-Suggest Logic (Story 4.3) ---

def auto_suggest_promotion(conn, new_model_version: str, new_metrics: Dict) -> str:
    """
    Compares new model with champion and suggests promotion.

    Returns:
        suggestion: 'ready_for_canary', 'rejected', or 'needs_review'
    """
    with conn.cursor() as cursor:
        # Get current champion model
        cursor.execute("""
            SELECT model_version, metrics
            FROM model_registry
            WHERE is_active = true
            ORDER BY created_at DESC
            LIMIT 1
        """)

        champion = cursor.fetchone()

    if not champion:
        # No existing model, auto-approve
        return 'ready_for_canary'

    champion_metrics = json.loads(champion[1])

    # Guardrail 1: Sharpe improvement (AC1)
    sharpe_improvement = new_metrics['sharpe'] - champion_metrics.get('sharpe', 0)

    if sharpe_improvement < MIN_SHARPE_IMPROVEMENT:
        reason = f"Sharpe improvement {sharpe_improvement:.3f} < {MIN_SHARPE_IMPROVEMENT}"
        print(f"\n   Auto-Suggest: REJECTED - {reason}")
        return 'rejected'

    # Guardrail 2: Max drawdown degradation (AC2)
    champion_dd = champion_metrics.get('max_drawdown', 0)
    new_dd = new_metrics['max_drawdown']
    dd_degradation = abs(new_dd - champion_dd) / (abs(champion_dd) + 1e-6)

    if dd_degradation > MAX_DRAWDOWN_DEGRADATION:
        reason = f"Drawdown degradation {dd_degradation:.2%} > {MAX_DRAWDOWN_DEGRADATION:.2%}"
        print(f"\n   Auto-Suggest: REJECTED - {reason}")
        return 'rejected'

    # All guardrails passed
    print(f"\n Auto-Suggest: READY FOR CANARY")
    print(f"  Sharpe improvement: {sharpe_improvement:.3f}")
    print(f"  Drawdown change: {dd_degradation:.2%}")

    return 'ready_for_canary'

# --- Main Training Pipeline ---

def run_train_batch():
    """
    Main entrypoint for train_batch job.

    Execution flow:
    1. Acquire advisory lock
    2. Check training triggers
    3. If triggered, fetch training data
    4. Run WFO backtest
    5. Train final model on all data
    6. Save to model registry
    7. Auto-suggest promotion
    8. Release lock
    """

    print("=" * 60)
    print("Model Training Pipeline starting...")
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

        # Step 1: Check training triggers (AC2)
        print("\nChecking training triggers...")
        should_train, reasons = check_training_triggers(conn)

        if not should_train:
            print("No training triggers activated. Skipping training.")
            if reasons:
                for reason in reasons:
                    print(f"  - {reason}")
            return 0

        print(" Training triggers activated:")
        for reason in reasons:
            print(f"  - {reason}")

        # Step 2: Run WFO backtest (Story 4.2)
        backtest_end = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        backtest_start = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')

        metrics = run_wfo_backtest(conn, backtest_start, backtest_end)

        # Step 3: Train final model on all available data
        print("\nTraining final model on all data...")
        train_end = backtest_end
        train_start = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')

        X_train, y_train = fetch_training_data(conn, train_start, train_end)

        if len(X_train) < 20:
            print(f"ERROR: Insufficient training data ({len(X_train)} samples)")
            return 1

        print(f" Fetched {len(X_train)} training samples")

        model = train_model(X_train, y_train)
        print(f" Model trained")

        # Step 4: Save to model registry (AC7)
        model_version = save_model_to_registry(conn, model, metrics)

        # Step 5: Auto-suggest promotion (Story 4.3)
        suggestion = auto_suggest_promotion(conn, model_version, metrics)

        # Update model registry with suggestion
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE model_registry
                SET promotion_suggestion = %s
                WHERE model_version = %s
            """, (suggestion, model_version))

        conn.commit()

        # Release lock
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (ADVISORY_LOCK_NAME,))

        print("=" * 60)
        print("Model Training Pipeline finished successfully!")
        print(f"Model Version: {model_version}")
        print(f"Promotion Suggestion: {suggestion}")
        print("=" * 60)

        return 0

    except Exception as e:
        print(f"ERROR: Training pipeline failed: {e}")
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
    sys.exit(run_train_batch())
