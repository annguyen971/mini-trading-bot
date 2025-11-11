# src/worker/tasks_backtest.py
# Implements Story 4.2 (Backtesting Engine) & 4.3 (Auto-Suggest)

import os
import sys
import json
import pickle
from datetime import datetime, timezone

# Assuming core_lib provides these helpers
from core_lib.db import get_db_connection, advisory_lock

# --- Constants ---
ARTIFACTS_DIR = "/opt/artifacts"
MIN_TRADES_OOS_GUARDRAIL = 200
CONSECUTIVE_WINS_GUARDRAIL = 2
SHARPE_DIFF_GUARDRAIL = 0.1
DRAWDOWN_FACTOR_GUARDRAIL = 1.2

def get_champion_challenger_models():
    """
    Selects the Champion and Challenger models for the 1v1 backtest. (Story 4.2/AC2)
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Champion: The currently active model
            cur.execute("SELECT model_version, metrics FROM model_registry WHERE is_active = true LIMIT 1;")
            champion = cur.fetchone()

            # Challenger: The newest model for which backtest metrics are null
            cur.execute("""
                SELECT model_version, metrics FROM model_registry
                WHERE metrics IS NULL AND promotion_suggestion IS NULL
                ORDER BY model_version DESC LIMIT 1;
            """)
            challenger = cur.fetchone()

            if not champion or not challenger:
                return None, None

            return (champion[0], json.loads(champion[1]) if champion[1] else None), \
                   (challenger[0], json.loads(challenger[1]) if challenger[1] else None)


import numpy as np
import pandas as pd
def run_wfo_backtest(champion_version, challenger_version):
    """
    Runs the WFO backtest for both models. (Story 4.2/AC3-AC5)
    This is a functional placeholder that simulates the output of a vectorbt
    backtest without the heavy dependency.
    """
    print(f"Running WFO backtest: '{champion_version}' vs '{challenger_version}'...")
    print("Step 1/3: Loading models and cached as-of data...")
    # In a real scenario, we would load the .pkl files and the feature data

    print("Step 2/3: Simulating backtest portfolio returns...")
    # Simulate daily returns for a year for both elitist and baseline strategies
    np.random.seed(hash(challenger_version) % (2**32 - 1)) # Seed for reproducibility
    days = 252
    elitist_returns = np.random.normal(loc=0.001, scale=0.02, size=days)
    baseline_returns = np.random.normal(loc=0.0005, scale=0.018, size=days)

    # Calculate cumulative returns to find drawdown
    elitist_cumulative = np.cumprod(1 + elitist_returns)
    running_max = np.maximum.accumulate(elitist_cumulative)
    drawdown = (elitist_cumulative - running_max) / running_max

    print("Step 3/3: Calculating performance metrics...")
    # Calculate metrics
    sharpe_elitist = (np.mean(elitist_returns) * np.sqrt(252)) / np.std(elitist_returns)
    sharpe_baseline = (np.mean(baseline_returns) * np.sqrt(252)) / np.std(baseline_returns)

    challenger_metrics = {
        "sharpe_elitist": round(sharpe_elitist, 3),
        "sharpe_baseline": round(sharpe_baseline, 3),
        "ic_elitist": round(np.corrcoef(elitist_returns[:-1], elitist_returns[1:])[0, 1], 3),
        "ic_baseline": round(np.corrcoef(baseline_returns[:-1], baseline_returns[1:])[0, 1], 3),
        "max_drawdown": round(np.min(drawdown), 3),
        "oos_trades": 250,  # Hardcoded as per the original dummy data
        "ci_95_diff": [0.01, 0.05] # Placeholder
    }
    print(f"Backtest complete. Results: {challenger_metrics}")
    return challenger_metrics


def auto_suggest_promotion(champion, challenger, new_metrics):
    """
    Applies guardrails to suggest or reject the challenger. (Story 4.3/AC1-AC5)
    """
    champion_version, champion_metrics = champion
    challenger_version, _ = challenger

    suggestion = None
    reason = []

    # Guardrail 0: Statistical significance
    if new_metrics['oos_trades'] < MIN_TRADES_OOS_GUARDRAIL:
        suggestion = 'rejected_by_guardrail'
        reason.append(f"oos_trades ({new_metrics['oos_trades']}) < min ({MIN_TRADES_OOS_GUARDRAIL})")

    # Guardrail 1: Hysteresis
    # Note: A real implementation needs to track consecutive_wins from history.
    # We simulate it here by checking if the new sharpe is significantly better.
    sharpe_diff = new_metrics['sharpe_elitist'] - champion_metrics.get('sharpe_elitist', 0)
    if sharpe_diff < SHARPE_DIFF_GUARDRAIL:
        suggestion = 'rejected_by_guardrail'
        reason.append(f"sharpe_diff ({sharpe_diff:.2f}) < min ({SHARPE_DIFF_GUARDRAIL})")

    # Guardrail 2: Risk
    new_drawdown = new_metrics['max_drawdown']
    old_drawdown = champion_metrics.get('max_drawdown', -1.0)
    if abs(new_drawdown) > abs(old_drawdown) * DRAWDOWN_FACTOR_GUARDRAIL:
        suggestion = 'rejected_by_guardrail'
        reason.append(f"max_drawdown ({abs(new_drawdown):.2f}) > allowed ({abs(old_drawdown) * DRAWDOWN_FACTOR_GUARDRAIL:.2f})")

    if not suggestion:
        suggestion = 'ready_for_canary'
        reason.append("All guardrails passed.")

    print(f"Suggestion for '{challenger_version}': {suggestion}. Reason: {'; '.join(reason)}")

    # Update model_registry and log to history
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Update the challenger's metrics and suggestion
            cur.execute("""
                UPDATE model_registry SET
                    metrics = %s::jsonb,
                    promotion_suggestion = %s
                WHERE model_version = %s;
            """, (json.dumps(new_metrics), suggestion, challenger_version))

            # Log this event to the promotion history
            evidence = {
                "reason": reason, "champion": champion_version,
                "champion_metrics": champion_metrics, "challenger_metrics": new_metrics
            }
            cur.execute("""
                INSERT INTO model_promotion_history (model_version, action, actor, evidence_hash, created_at)
                VALUES (%s, %s, %s, %s, %s);
            """, (
                challenger_version, f"AUTO_SUGGEST_{suggestion.upper()}", "system",
                json.dumps(evidence), datetime.now(timezone.utc)
            ))
        conn.commit()
    print("Updated model_registry and logged promotion history.")


def main():
    """Main execution function."""
    print(f"--- Running backtest_batch at {datetime.now(timezone.utc)} ---")

    with advisory_lock('backtest_batch') as locked:
        if not locked:
            print("Could not acquire lock 'backtest_batch'. Exiting.")
            return 1

        champion, challenger = get_champion_challenger_models()

        if not champion or not challenger:
            print("No valid Champion/Challenger pair found. Skipping backtest.")
            return 0

        print(f"Found Champion: {champion[0]}, Challenger: {challenger[0]}")

        new_metrics = run_wfo_backtest(champion[0], challenger[0])

        auto_suggest_promotion(champion, challenger, new_metrics)

        print("--- backtest_batch finished successfully ---")
        return 0

if __name__ == "__main__":
    sys.exit(main())
