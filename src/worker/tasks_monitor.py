import os
from contextlib import contextmanager
import psycopg
from core_lib.locks import try_lock

@contextmanager
def pg_conn():
    with psycopg.connect(os.getenv("PG_DSN")) as conn:
        conn.autocommit = False
        yield conn

def get_canary_and_champion(conn):
    with conn.cursor() as cur:
        # A canary is a model that is active but is NOT the latest model marked as a full champion.
        # This is a simplification; a real system would have explicit statuses.
        cur.execute("SELECT model_version FROM model_registry WHERE is_active = true ORDER BY created_at DESC;")
        active_models = cur.fetchall()

        if len(active_models) < 2:
            return None, None # Not enough models for a canary/champion setup

        canary = active_models[0][0]
        champion = active_models[1][0]

        return canary, champion

def check_canary_performance(conn, canary_version):
    # Check live performance metrics from the last hour
    with conn.cursor() as cur:
        cur.execute("""
            SELECT value FROM monitoring_logs
            WHERE metric_name = 'live_max_drawdown'
            AND model_version = %s
            AND log_time >= NOW() - INTERVAL '1 hour';
        """, (canary_version,))
        drawdown = cur.fetchone()

        # Violation if drawdown exceeds a threshold (e.g., 20%)
        if drawdown and drawdown[0] > 0.20:
            return True # Performance is bad

    return False # Performance is OK

def rollback_canary(conn, canary_version, champion_version):
    with conn.cursor() as cur:
        # Deactivate canary
        cur.execute("UPDATE model_registry SET is_active = false, promotion_suggestion = 'rolled_back' WHERE model_version = %s;", (canary_version,))
        # Reactivate champion
        if champion_version:
            cur.execute("UPDATE model_registry SET is_active = true WHERE model_version = %s;", (champion_version,))

        # Log the rollback event
        cur.execute("""
            INSERT INTO model_promotion_history (model_version, action, actor)
            VALUES (%s, 'ROLLBACK', 'system');
        """, (canary_version,))
        print(f"ALERT: Canary model {canary_version} performance violation. Rolled back.")

def run_monitor_canary_batch():
    lock_name = 'monitor_canary_batch'
    with pg_conn() as conn:
        if not try_lock(conn, lock_name):
            print(f"Could not acquire lock {lock_name}. Exiting.")
            return

        try:
            canary, champion = get_canary_and_champion(conn)
            if not canary:
                print("No active canary found. Exiting.")
                return

            if check_canary_performance(conn, canary):
                rollback_canary(conn, canary, champion)
            else:
                print(f"Canary {canary} performance is OK.")

            conn.commit()

        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
            conn.commit()

if __name__ == "__main__":
    run_monitor_canary_batch()
