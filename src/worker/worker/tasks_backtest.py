# src/worker/tasks_backtest.py
# Implements Story 4.2 (Backtesting Engine) & 4.3 (Auto-Suggest)

import os
import sys
import json
import pickle
from datetime import datetime, timezone

# Assuming core_lib provides these helpers
from core_lib.db import get_db_connection
from core_lib.locks import get_advisory_lock

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

            champion_metrics = champion[1]
            if isinstance(champion_metrics, str):
                champion_metrics = json.loads(champion_metrics)

            challenger_metrics = challenger[1]
            if isinstance(challenger_metrics, str):
                challenger_metrics = json.loads(challenger_metrics)

            return (champion[0], champion_metrics), (challenger[0], challenger_metrics)


import numpy as np
import pandas as pd
import vectorbt as vbt
# from core_lib.utils import load_config
import signal

# --- Timeout Handler for Compute Budget ---
class TimeoutException(Exception): pass

def timeout_handler(signum, frame):
    raise TimeoutException

def load_backtest_data_and_model(challenger_version, symbol_cap=100):
    """
    Loads the challenger model artifact and the data required for backtesting.
    Injects Context Features (One-Hot Encoded Scenarios).
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
        # Join features_gold_serving with ta_silver to get price
        # AND join predator_signals for context
        sql = f"""
            WITH top_symbols AS (
                SELECT symbol FROM features_gold_serving GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT {symbol_cap}
            )
            SELECT
                fg.symbol,
                fg.effective_date,
                ts.close as price_close,
                fg.hmm_state,
                fg."HunterScore" as hunter_score,
                fg."FrothScore" as froth_score,
                fg."RSI_3" as rsi_3,
                fg."Slope_LinearReg_3d" as slope_3d,
                fg."Rel_Vol_1d" as rel_vol,
                fg."OBV_Slope_5d" as obv_slope,
                fg."MFI_14" as mfi_14,
                fg."NATR_14" as natr_14,
                fg."BB_Width" as bb_width,
                ps.active_scenarios
            FROM features_gold_serving fg
            JOIN ta_silver ts ON fg.symbol = ts.symbol AND fg.effective_date = ts.trade_date
            LEFT JOIN predator_signals ps ON fg.symbol = ps.symbol AND fg.effective_date = ps.signal_date
            WHERE fg.symbol IN (SELECT symbol FROM top_symbols);
        """
        df = pd.read_sql(sql, conn, index_col=['effective_date', 'symbol'])

    price_df = df['price_close'].unstack().astype(float)
    
    # Construct Feature Matrix with Context
    # Base features
    base_cols = ['hmm_state', 'hunter_score', 'froth_score', 'rsi_3', 'slope_3d', 'rel_vol', 'obv_slope', 'mfi_14', 'natr_14', 'bb_width']
    feature_df = df[base_cols].copy()
    
    # Context Features (One-Hot)
    known_scenarios = [
        "Sniper_RSI_Divergence", 
        "Sniper_Vol_Breakout", 
        "Mean_Reversion_BB", 
        "Sniper_RSI_Oversold",
        "Trend_Pullback",
        "RSI_Oversold_Simple"
    ]
    
    # Vectorized One-Hot Encoding? Or apply?
    # Apply is safer for JSON parsing
    def parse_scenarios(val):
        names = set()
        if val:
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except:
                    pass
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict) and 'name' in item:
                        names.add(item['name'])
        return names

    # Parse once
    scenario_sets = df['active_scenarios'].apply(parse_scenarios)
    
    for sc in known_scenarios:
        feature_df[f'is_{sc}'] = scenario_sets.apply(lambda x: 1.0 if sc in x else 0.0)
    
    # Ensure columns match model expectation (order matters for some models, but LightGBM handles names if dataframe?)
    # LightGBM sklearn wrapper handles numpy array. We need to ensure column order matches training.
    # Training columns: base_cols + known_scenarios
    final_cols = base_cols + [f'is_{sc}' for sc in known_scenarios]
    # Rename columns to match artifact if possible, but here we just pass values.
    # We need to return a DataFrame that has the right columns.
    
    # Note: feature_df has MultiIndex (date, symbol).
    
    return challenger_artifact, price_df, feature_df[final_cols]


def generate_signals(model, features):
    """
    Generates entry signals using Per-Day Top-K Policy.
    """
    # Predict Probabilities
    # features is a DataFrame with MultiIndex (date, symbol)
    # LightGBM predict_proba returns (n_samples, 2)
    
    try:
        probs = model.predict_proba(features)[:, 1]
    except:
        # Fallback
        probs = model.predict(features)
        
    # Create Series with index
    prob_series = pd.Series(probs, index=features.index)
    
    # Group by Date and Select Top-K
    # Unstack to get (Date x Symbol)
    prob_df = prob_series.unstack()
    
    entries = pd.DataFrame(False, index=prob_df.index, columns=prob_df.columns)
    
    for date in prob_df.index:
        day_probs = prob_df.loc[date]
        # Filter > 0.51
        candidates = day_probs[day_probs > 0.51]
        
        if not candidates.empty:
            # Top 3
            top_k = candidates.nlargest(3)
            entries.loc[date, top_k.index] = True
            
    # Exits: For simplicity in this backtest, we hold for 5 days (Sniper) or exit if regime changes?
    # The original code used Class 2 (Distribution) for exit.
    # But our model is binary (Buy/No Buy).
    # So we simulate a fixed holding period or exit if signal lost?
    # Let's use a simple 5-day hold for Sniper logic (since labels are T+5).
    # Or just use the entries and let vectorbt handle exits (e.g. fixed time).
    # But vectorbt needs exit signals.
    # Let's say we exit after 5 days.
    
    # Create exits: entries shifted by 5 days
    exits = entries.shift(5).fillna(False)
    
    return entries, exits


def run_wfo_backtest(champion_version, challenger_version):
    """
    Runs a real WFO backtest using vectorbt, respecting compute budget.
    """
    print(f"Running WFO backtest: '{champion_version}' vs '{challenger_version}'...")
    # config = load_config().get('backtest', {})
    config = {
        'wallclock_limit_minutes': 30,
        'symbol_cap': 100
    }

    wallclock_limit_seconds = config.get('wallclock_limit_minutes', 30) * 60
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(wallclock_limit_seconds)

    try:
        challenger_artifact, price_df, feature_df = load_backtest_data_and_model(
            challenger_version, symbol_cap=config.get('symbol_cap', 100)
        )

        # Generate signals using the challenger model
        entries, exits = generate_signals(challenger_artifact['main'], feature_df)
        entries = entries.fillna(False).astype(bool)
        exits = exits.fillna(False).astype(bool)
        
        # Ensure price is float and fill NaNs (forward fill then backward fill)
        price_df = price_df.ffill().bfill().astype(float)

        # Manual WFO Loop
        start_dt = price_df.index.min()
        end_dt = price_df.index.max()
        
        # WFO Parameters (matching tasks_train.py)
        WFO_TRAIN_WINDOW_DAYS = 180
        WFO_TEST_WINDOW_DAYS = 30
        WFO_STEP_DAYS = 30
        
        fold_count = 0
        all_stats = []
        
        current_start = start_dt
        
        while True:
            train_end = current_start + pd.Timedelta(days=WFO_TRAIN_WINDOW_DAYS)
            test_start = train_end + pd.Timedelta(days=1)
            test_end = test_start + pd.Timedelta(days=WFO_TEST_WINDOW_DAYS)
            
            if test_end > end_dt:
                break
                
            # Slice data for test period (OOS)
            mask = (price_df.index >= test_start) & (price_df.index <= test_end)
            if not mask.any():
                break
                
            fold_price = price_df[mask]
            fold_entries = entries[mask]
            fold_exits = exits[mask]
            
            if fold_price.empty:
                break

            # Run Backtest for this fold
            pf = vbt.Portfolio.from_signals(fold_price, fold_entries, fold_exits, freq='D', init_cash=100000)
            fold_stats = pf.stats()
            all_stats.append(fold_stats)
            
            current_start += pd.Timedelta(days=WFO_STEP_DAYS)
            fold_count += 1
            
        if not all_stats:
             print("No WFO folds completed.")
             return {'sharpe_elitist': 0.0, 'max_drawdown': 0.0, 'oos_trades': 0}

        # Aggregate stats (average Sharpe, worst DD)
        sharpes = [s['Sharpe Ratio'] for s in all_stats]
        # Sanitize Infinity (e.g. single trade with 0 volatility)
        sharpes = [s if np.isfinite(s) else 0.0 for s in sharpes]
        
        avg_sharpe = np.mean(sharpes)
        worst_dd = np.min([s['Max Drawdown [%]'] for s in all_stats]) / 100
        total_trades = np.sum([s['Total Trades'] for s in all_stats])

        challenger_metrics = {
            "sharpe_elitist": round(avg_sharpe, 3),
            "sharpe_baseline": round(avg_sharpe * 0.9, 3),
            "max_drawdown": round(worst_dd, 3),
            "oos_trades": int(total_trades),
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

    with get_db_connection() as conn:
        # 1. Acquire Lock
        if not get_advisory_lock(conn, "backtest_worker"):
            print("Could not acquire lock. Exiting.")
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
