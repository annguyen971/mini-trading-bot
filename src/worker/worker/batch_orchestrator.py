#!/usr/bin/env python3
"""
Batch Job Orchestrator - Stock Hunter AI
=========================================
Routes and executes batch processing tasks from task_q

Handles task kinds:
- NLP_BATCH: Sentiment analysis via Gemini API
- FEATURE_GOLD_BATCH: Compute HMM, Hunter, Froth scores
- LABEL_BATCH: Apply Snorkel weak supervision
- TRAIN_BATCH: Model training with WFO

This is the missing orchestrator that enables the full ML pipeline.
Run via cron or as standalone worker.
"""

import sys
from core_lib.db import get_db_connection
from core_lib.locks import acquire_advisory_lock, release_advisory_lock


def process_batch_task(conn, task_kind: str) -> bool:
    """
    Route and execute a batch task based on kind.

    Args:
        conn: Database connection
        task_kind: Type of batch job

    Returns:
        True if task executed successfully, False otherwise
    """
    print(f"Routing task: {task_kind}")

    try:
        if task_kind == "NLP_BATCH":
            from worker.tasks_nlp import run_nlp_pipeline
            print("→ Executing NLP batch (Gemini API sentiment analysis)")
            run_nlp_pipeline()
            return True

        elif task_kind == "FEATURE_GOLD_BATCH":
            from worker.tasks_feature_gold import run_feature_gold_batch
            print("→ Executing Feature Gold batch (HMM + Scoring)")
            run_feature_gold_batch(conn)
            return True

        elif task_kind == "LABEL_BATCH":
            from worker.tasks_label import run_label_pipeline
            print("→ Executing Label batch (Snorkel + Active Learning)")
            run_label_pipeline(conn)
            return True

        elif task_kind == "TRAIN_BATCH":
            from worker.tasks_train import run_training_pipeline
            print("→ Executing Train batch (WFO + Model Registry)")
            run_training_pipeline(conn)
            return True

        else:
            print(f"⚠ Unknown batch task kind: {task_kind}")
            return False

    except Exception as e:
        print(f"✗ Batch task {task_kind} failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_batch_worker():
    """
    Main batch worker loop.

    Polls task_q for batch jobs and executes them.
    Uses advisory locks to prevent concurrent execution.
    """
    print("=" * 60)
    print("Batch Orchestrator Worker starting...")
    print("=" * 60)

    LOCK_NAME = "batch_orchestrator_lock"
    conn = None

    try:
        conn = get_db_connection()

        # Acquire lock to ensure single-worker execution
        if not acquire_advisory_lock(conn, LOCK_NAME):
            print("⚠ Another batch worker is running. Exiting.")
            return 0

        print("✓ Acquired advisory lock")

        # Fetch batch tasks from task_q
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, kind, payload
                FROM task_q
                WHERE kind IN ('NLP_BATCH', 'FEATURE_GOLD_BATCH', 'LABEL_BATCH', 'TRAIN_BATCH')
                  AND next_run_at <= NOW()
                ORDER BY next_run_at
                LIMIT 10
                FOR UPDATE SKIP LOCKED
            """)

            tasks = cur.fetchall()

            if not tasks:
                print("No batch tasks pending.")
                return 0

            print(f"Found {len(tasks)} batch tasks to process")

        # Process each task
        for task_id, task_kind, payload in tasks:
            print(f"\n--- Task {task_id}: {task_kind} ---")

            success = process_batch_task(conn, task_kind)

            # Update task status
            with conn.cursor() as cur:
                if success:
                    # Remove from queue (batch jobs are one-time)
                    cur.execute("DELETE FROM task_q WHERE id = %s", (task_id,))
                    print(f"✓ Task {task_id} completed and removed from queue")
                else:
                    # Move to DLQ
                    cur.execute("""
                        INSERT INTO task_q_dlq (kind, payload, reason, moved_at)
                        SELECT kind, payload, 'batch_execution_failed', NOW()
                        FROM task_q WHERE id = %s
                    """, (task_id,))
                    cur.execute("DELETE FROM task_q WHERE id = %s", (task_id,))
                    print(f"✗ Task {task_id} failed - moved to DLQ")

            conn.commit()

        print(f"\n✓ Processed {len(tasks)} batch tasks")

    except Exception as e:
        print(f"ERROR: Batch orchestrator failed: {e}")
        if conn:
            conn.rollback()
        import traceback
        traceback.print_exc()
        return 1

    finally:
        if conn:
            try:
                release_advisory_lock(conn, LOCK_NAME)
                conn.close()
            except Exception as e:
                print(f"WARN: Cleanup failed: {e}")

        print("=" * 60)
        print("Batch Orchestrator Worker finished.")
        print("=" * 60)

    return 0


def run_single_batch(task_kind: str):
    """
    Run a single batch job directly (for cron or manual execution).

    Args:
        task_kind: NLP_BATCH, FEATURE_GOLD_BATCH, LABEL_BATCH, or TRAIN_BATCH

    Example:
        python -m worker.batch_orchestrator --task FEATURE_GOLD_BATCH
    """
    print(f"Running single batch job: {task_kind}")

    conn = None
    try:
        conn = get_db_connection()
        success = process_batch_task(conn, task_kind)

        if success:
            print(f"✓ {task_kind} completed successfully")
            return 0
        else:
            print(f"✗ {task_kind} failed")
            return 1

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Batch Job Orchestrator")
    parser.add_argument(
        '--task',
        type=str,
        choices=['NLP_BATCH', 'FEATURE_GOLD_BATCH', 'LABEL_BATCH', 'TRAIN_BATCH'],
        help='Run a single batch job'
    )
    parser.add_argument(
        '--worker',
        action='store_true',
        help='Run as continuous worker (polls task_q)'
    )

    args = parser.parse_args()

    if args.task:
        # Single batch job
        sys.exit(run_single_batch(args.task))
    elif args.worker:
        # Continuous worker mode
        sys.exit(run_batch_worker())
    else:
        # Default: run worker once
        sys.exit(run_batch_worker())
