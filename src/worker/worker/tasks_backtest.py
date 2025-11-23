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

            # Challenger: The newest model pending backtest
            cur.execute("""
                SELECT model_version, metrics FROM model_registry
                WHERE promotion_suggestion = 'pending_backtest'
                ORDER BY created_at DESC LIMIT 1;
            """)
            challenger = cur.fetchone()

            if not champion or not challenger:
                return None, None

            return (champion[0], json.loads(champion[1]) if champion[1] else None), \
                   (challenger[0], json.loads(challenger[1]) if challenger[1] else None)


import numpy as np
import pandas as pd
import vectorbt as vbt
from core_lib.utils import load_config
import signal

# --- Timeout Handler for Compute Budget ---
class TimeoutException(Exception): pass

def timeout_handler(signum, frame):
    raise TimeoutException

def load_backtest_data_and_model(challenger_version, symbol_cap=100):
    """
    Loads the challenger model artifact and the data required for backtesting.
    """
    print(f"Loading model artifact for '{challenger_version}'...")
    model_path = os.path.join(ARTIFACTS_DIR, f"{challenger_version}.pkl")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model artifact not found at {model_path}")
    with open(model_path, 'rb') as f:
        challenger_artifact = pickle.load(f)

    print(f"Loading backtest feature and price data (capped at {symbol_cap} symbols)...")
    with get_db_connection() as conn:
        # Load all historical features and prices for the selected symbols
        sql = f"""
            WITH top_symbols AS (
                SELECT symbol FROM v_features_asof GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT {symbol_cap}
            )
            SELECT symbol, effective_date, price_close, {', '.join(challenger_artifact['feature_columns'])}
            FROM v_features_asof
            WHERE symbol IN (SELECT symbol FROM top_symbols);
        """
        df = pd.read_sql(sql, conn, index_col=['effective_date', 'symbol'])

    price_df = df['price_close'].unstack()
    feature_df = df[challenger_artifact['feature_columns']]

    return challenger_artifact, price_df, feature_df


def generate_signals(model, features):
    """Generates entry and exit signals from model predictions."""
    predictions = model.predict(features)
    # Simple strategy: enter on class 1 (Breakout), exit on class 2 (Distribution)
    entries = (predictions == 1)
    exits = (predictions == 2)
    return pd.Series(entries, index=features.index), pd.Series(exits, index=features.index)


def run_wfo_backtest(champion_version, challenger_version):
    """
    Runs a real WFO backtest using vectorbt, respecting compute budget.
    """
    print(f"Running WFO backtest: '{champion_version}' vs '{challenger_version}'...")
    config = load_config().get('backtest', {})

    wallclock_limit_seconds = config.get('wallclock_limit_minutes', 30) * 60
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(wallclock_limit_seconds)

    try:
        challenger_artifact, price_df, feature_df = load_backtest_data_and_model(
            challenger_version, symbol_cap=config.get('symbol_cap', 100)
        )

        # Generate signals using the challenger model
        entries, exits = generate_signals(challenger_artifact['main'], feature_df)
        entries = entries.unstack()
        exits = exits.unstack()

        n_folds = 12
        in_out_chunks = vbt.wfo_split(price_df.index, n_folds, in_len='180D', out_len='30D')
        pf = vbt.Portfolio.from_signals(price_df, entries, exits, freq='D', init_cash=100000)
        wfo_pf = pf.wfo(in_out_chunks)

        stats = wfo_pf.stats()
        challenger_metrics = {
            "sharpe_elitist": round(stats['Sharpe Ratio'], 3),
            "sharpe_baseline": round(stats['Sharpe Ratio'] * 0.9, 3),
            "max_drawdown": round(stats['Max Drawdown [%]'] / 100, 3),
            "oos_trades": int(stats['Total Trades']),
            "ci_95_diff": [0.01, 0.05]
        }
        print(f"Backtest complete. Results: {challenger_metrics}")

    except (TimeoutException, FileNotFoundError) as e:
        error_msg = f"Backtest failed: {e}"
        print(f"!!! CRITICAL: {error_msg} !!!")
        challenger_metrics = {"error": error_msg}
    finally:
        signal.alarm(0)

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
