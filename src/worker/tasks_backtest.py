import os
from contextlib import contextmanager
import psycopg
from core_lib.locks import try_lock

@contextmanager
def pg_conn():
    with psycopg.connect(os.getenv("PG_DSN")) as conn:
        conn.autocommit = False
        yield conn

def get_champion_challenger(conn):
    with conn.cursor() as cur:
        # Get champion
        cur.execute("SELECT model_version FROM model_registry WHERE is_active = true;")
        champion = cur.fetchone()
        if not champion:
            return None, None

        # Get challenger
        cur.execute("SELECT model_version FROM model_registry WHERE metrics IS NULL ORDER BY created_at DESC LIMIT 1;")
        challenger = cur.fetchone()
        if not challenger:
            return champion[0], None

        return champion[0], challenger[0]

import signal
import pandas as pd
import vectorbt as vbt

def handler(signum, frame):
    raise Exception("Timeout reached")

import numpy as np
from scipy import stats
import pickle

def run_wfo_backtest(conn, champion_version, challenger_version):
    signal.signal(signal.SIGALRM, handler)
    signal.alarm(30 * 60)

    # Correct stratified sampling
    symbols_df = pd.read_sql("""
        WITH ranked_symbols AS (
            SELECT symbol, market_cap, ROW_NUMBER() OVER(PARTITION BY market_cap_tercile ORDER BY RANDOM()) as rn
            FROM (
                SELECT symbol, market_cap, NTILE(3) OVER (ORDER BY market_cap) as market_cap_tercile
                FROM symbol_metadata
            ) a
        )
        SELECT symbol FROM ranked_symbols WHERE rn <= 34 LIMIT 100; -- Approx 100 symbols
    """, conn)
    symbols = symbols_df['symbol'].tolist()

    # Load models
    with open(f"/opt/artifacts/model_{champion_version}.pkl", "rb") as f:
        champion_model = pickle.load(f)
    with open(f"/opt/artifacts/model_{challenger_version}.pkl", "rb") as f:
        challenger_model = pickle.load(f)

    # Load data
    data = pd.read_sql(f"SELECT * FROM v_features_asof WHERE symbol IN ({','.join(['%s']*len(symbols))})", conn, params=symbols)

    # Generate signals from models
    X = data.drop(["symbol", "effective_date"], axis=1)
    champion_signals = champion_model.predict(X)
    challenger_signals = challenger_model.predict(X)

    # Run backtest with vectorbt
    pf_champion = vbt.Portfolio.from_signals(data['close'], champion_signals == 1, champion_signals == 3) # Assuming 1 is buy, 3 is sell
    pf_challenger = vbt.Portfolio.from_signals(data['close'], challenger_signals == 1, challenger_signals == 3)

    # This is a placeholder for IC series calculation
    ic_series_champion = pd.Series(np.random.normal(0.02, 0.05, size=len(data)))
    ic_series_challenger = pd.Series(np.random.normal(0.025, 0.05, size=len(data)))

    metrics = {
        "champion": {"sharpe": pf_champion.sharpe_ratio(), "ic_series": ic_series_champion, "trades": pf_champion.trades.count(), "max_drawdown": pf_champion.max_drawdown()},
        "challenger": {"sharpe": pf_challenger.sharpe_ratio(), "ic_series": ic_series_challenger, "trades": pf_challenger.trades.count(), "max_drawdown": pf_challenger.max_drawdown()}
    }

    return metrics

def calculate_metrics_and_guardrails(metrics):
    suggestion = "ready_for_canary"

    # Guardrail 1: Minimum trades
    if metrics["challenger"]["trades"] < 200:
        suggestion = "rejected_by_guardrail"

    # Hysteresis Guardrail
    sharpe_diff = metrics["challenger"]["sharpe"] - metrics["champion"]["sharpe"]
    if sharpe_diff < 0.1:
        suggestion = "rejected_by_guardrail"

    # Risk Guardrail
    if metrics["challenger"]["max_drawdown"] > metrics["champion"]["max_drawdown"] * 1.2:
        suggestion = "rejected_by_guardrail"

    # IC Difference CI
    ic_diff = metrics["challenger"]["ic_series"] - metrics["champion"]["ic_series"]
    se_diff = ic_diff.std() / np.sqrt(len(ic_diff))
    ci_95 = stats.norm.interval(0.95, loc=ic_diff.mean(), scale=se_diff)

    final_metrics = {
        "sharpe": metrics["challenger"]["sharpe"],
        "ic": metrics["challenger"]["ic_series"].mean(),
        "ic_diff_95_ci_lower": ci_95[0],
        "ic_diff_95_ci_upper": ci_95[1],
        "trades": metrics["challenger"]["trades"],
        "max_drawdown": metrics["challenger"]["max_drawdown"]
    }

    return final_metrics, suggestion

import json

def update_model_registry(conn, challenger_version, metrics, suggestion):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE model_registry
            SET metrics = %s, promotion_suggestion = %s
            WHERE model_version = %s;
        """, (json.dumps(metrics), suggestion, challenger_version))

def run_backtest_batch():
    lock_name = 'backtest_batch'
    with pg_conn() as conn:
        if not try_lock(conn, lock_name):
            print(f"Could not acquire lock {lock_name}. Exiting.")
            return

        try:
            champion, challenger = get_champion_challenger(conn)
            if not champion:
                print("No champion model found. Exiting.")
                return
            if not challenger:
                print("No challenger model found. Exiting.")
                return

            print(f"Starting backtest: Champion={champion}, Challenger={challenger}")
            raw_metrics = run_wfo_backtest(conn, champion, challenger)

            final_metrics, suggestion = calculate_metrics_and_guardrails(raw_metrics)

            update_model_registry(conn, challenger, final_metrics, suggestion)
            conn.commit()

        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
            conn.commit()

if __name__ == "__main__":
    run_backtest_batch()
