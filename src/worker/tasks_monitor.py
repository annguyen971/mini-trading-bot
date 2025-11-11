# src/worker/tasks_monitor.py
# Implements Story 4.3 (Auto-Rollback)

import os
import sys
import json
from datetime import datetime, timezone

# Assuming core_lib provides these helpers
from core_lib.db import get_db_connection, advisory_lock

# --- Constants ---
# These would be tuned based on backtest results
CANARY_DRAWDOWN_GUARDRAIL = -0.15 # Max allowed drawdown for a canary model
CANARY_IC_GAP_GUARDRAIL = -0.05   # Max allowed drop in IC vs champion's baseline

def get_active_canary_and_champion():
    """
    Finds the active canary model and the main champion model.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Find a model marked as 'canary'
            cur.execute("SELECT model_version FROM model_registry WHERE is_active = 'canary' LIMIT 1;")
            canary = cur.fetchone()

            # Find the main production model
            cur.execute("SELECT model_version, metrics FROM model_registry WHERE is_active = true LIMIT 1;")
            champion = cur.fetchone()

            if not canary or not champion:
                return None, None

            return (canary[0],), (champion[0], json.loads(champion[1]) if champion[1] else {})

def check_canary_guardrails(canary_version, champion_metrics):
    """
    Checks live performance metrics for the canary against predefined guardrails.
    (Story 4.3/AC7)

    NOTE: This is a simplified simulation. A real system would calculate these metrics
    from live prediction logs and market data. Here, we'll query monitoring_logs.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Check Drawdown Guardrail
            cur.execute("""
                SELECT value FROM monitoring_logs
                WHERE metric_name = 'live_drawdown' AND metadata->>'model_version' = %s
                ORDER BY log_time DESC LIMIT 1;
            """, (canary_version,))
            live_drawdown = cur.fetchone()
            if live_drawdown and live_drawdown[0] < CANARY_DRAWDOWN_GUARDRAIL:
                return True, f"Drawdown guardrail breached ({live_drawdown[0]:.3f} < {CANARY_DRAWDOWN_GUARDRAIL})"

            # Check IC Gap Guardrail
            cur.execute("""
                SELECT value FROM monitoring_logs
                WHERE metric_name = 'live_ic_7d' AND metadata->>'model_version' = %s
                ORDER BY log_time DESC LIMIT 1;
            """, (canary_version,))
            live_ic = cur.fetchone()
            baseline_ic = champion_metrics.get('ic_elitist', 0)
            if live_ic and (live_ic[0] - baseline_ic) < CANARY_IC_GAP_GUARDRAIL:
                return True, f"IC Gap guardrail breached (live_ic={live_ic[0]:.3f}, baseline={baseline_ic:.3f})"

    return False, "All canary guardrails passed."


def perform_auto_rollback(canary_version, champion_version, reason):
    """
    Performs the auto-rollback by deactivating the canary and reactivating the champion.
    (Story 4.3/AC7)
    """
    print(f"!!! AUTO-ROLLBACK TRIGGERED for canary '{canary_version}' !!!")
    print(f"Reason: {reason}")
    print(f"Reactivating champion: '{champion_version}'")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Deactivate the canary model
            cur.execute("UPDATE model_registry SET is_active = false WHERE model_version = %s;", (canary_version,))

            # Reactivate the champion model
            cur.execute("UPDATE model_registry SET is_active = true WHERE model_version = %s;", (champion_version,))

            # Log this critical event to the history
            evidence = {"rollback_reason": reason}
            cur.execute("""
                INSERT INTO model_promotion_history (model_version, action, actor, evidence_hash, created_at)
                VALUES (%s, %s, %s, %s, %s);
            """, (
                canary_version, 'AUTO_ROLLBACK', 'system',
                json.dumps(evidence), datetime.now(timezone.utc)
            ))
        conn.commit()

    # In a real system, this would trigger a high-priority alert (e.g., PagerDuty, Telegram)
    print(f"ALERT: Canary model '{canary_version}' was automatically rolled back.")


def main():
    """Main execution function."""
    print(f"--- Running monitor_canary_batch at {datetime.now(timezone.utc)} ---")

    with advisory_lock('monitor_canary_batch') as locked:
        if not locked:
            print("Could not acquire lock 'monitor_canary_batch'. Exiting.")
            return 1

        canary, champion = get_active_canary_and_champion()

        if not canary:
            print("No active canary model found. Exiting.")
            return 0

        canary_version = canary[0]
        champion_version, champion_metrics = champion

        print(f"Monitoring canary: '{canary_version}' against champion: '{champion_version}'")

        breached, reason = check_canary_guardrails(canary_version, champion_metrics)

        if breached:
            perform_auto_rollback(canary_version, champion_version, reason)
        else:
            print(f"Canary '{canary_version}' is performing within guardrails.")

        print("--- monitor_canary_batch finished successfully ---")
        return 0

if __name__ == "__main__":
    sys.exit(main())
