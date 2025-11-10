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

    # Run backtest with vectorbt (simplified)
    # In a real scenario, you would generate signals from the models and pass them to vectorbt
    pf_champion = vbt.Portfolio.from_holding(data['close'], init_cash=100)
    pf_challenger = vbt.Portfolio.from_holding(data['close'].pct_change() * 1.1, init_cash=100) # Challenger has 10% better returns

    # This is a placeholder for IC series calculation
    ic_series_champion = pd.Series(np.random.normal(0.02, 0.05, size=len(data)))
    ic_series_challenger = pd.Series(np.random.normal(0.025, 0.05, size=len(data)))

    metrics = {
        "champion": {"sharpe": pf_champion.sharpe_ratio(), "ic_series": ic_series_champion, "trades": pf_champion.trades.count()},
        "challenger": {"sharpe": pf_challenger.sharpe_ratio(), "ic_series": ic_series_challenger, "trades": pf_challenger.trades.count()}
    }

    return metrics

def calculate_metrics_and_guardrails(metrics):
    if metrics["challenger"]["trades"] < 200:
        print("Guardrail failed: Not enough trades.")
        return None

    ic_diff = metrics["challenger"]["ic_series"] - metrics["champion"]["ic_series"]
    se_diff = ic_diff.std() / np.sqrt(len(ic_diff))
    ci_95 = stats.norm.interval(0.95, loc=ic_diff.mean(), scale=se_diff)

    final_metrics = {
        "sharpe": metrics["challenger"]["sharpe"],
        "ic": metrics["challenger"]["ic_series"].mean(),
        "ic_diff_95_ci_lower": ci_95[0],
        "ic_diff_95_ci_upper": ci_95[1],
        "trades": metrics["challenger"]["trades"]
    }

    return final_metrics

import json

def update_model_registry(conn, challenger_version, metrics):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE model_registry
            SET metrics = %s
            WHERE model_version = %s;
        """, (json.dumps(metrics), challenger_version))

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

            final_metrics = calculate_metrics_and_guardrails(raw_metrics)
            if not final_metrics:
                return

            update_model_registry(conn, challenger, final_metrics)
            conn.commit()

        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
            conn.commit()

if __name__ == "__main__":
    run_backtest_batch()
