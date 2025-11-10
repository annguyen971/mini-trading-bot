import json
from datetime import datetime

from core_lib.db import get_db_connection
from core_lib.locks import get_advisory_lock

# --- Constants ---
BACKPRESSURE_THRESHOLD = 10000
ADVISORY_LOCK_NAME = "silver_consume"

# --- Main Worker Logic ---

def set_backpressure_flag(conn, is_enabled: bool):
    """Sets the SCRAPE_SLOW flag in the control_flags table."""
    with conn.cursor() as cursor:
        query = """
            INSERT INTO control_flags (flag, enabled, updated_at)
            VALUES ('SCRAPE_SLOW', %s, %s)
            ON CONFLICT (flag) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = EXCLUDED.updated_at;
        """
        cursor.execute(query, (is_enabled, datetime.now()))
    print(f"Set SCRAPE_SLOW flag to: {is_enabled}")

def check_backpressure(conn):
    """(AC7) Checks task_q backlog and sets the backpressure flag if needed."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM task_q;")
        backlog = cursor.fetchone()[0]

        if backlog > BACKPRESSURE_THRESHOLD:
            set_backpressure_flag(conn, True)
        else:
            # Optional: Disable it if the backlog is clear
            set_backpressure_flag(conn, False)

def apply_sanity_rules(task_payload: dict) -> bool:
    """(AC3) Applies cheap sanity rules to the task data."""
    # This is a placeholder. A real implementation would be more robust.
    if not task_payload:
        return False
    if task_payload.get("symbol") is None and task_payload.get("text") is None:
        return False
    if task_payload.get("close", 1) < 0 or task_payload.get("volume", 1) < 0:
        return False
    if "text" in task_payload and len(task_payload.get("text", "")) < 30:
        return False
    return True

def move_task_to_dlq(conn, task_id: int, reason: str):
    """(AC4) Moves a failed task to the dead-letter queue."""
    with conn.cursor() as cursor:
        # This query atomically moves the row
        query = """
            WITH moved_task AS (
                DELETE FROM task_q WHERE id = %s RETURNING *
            )
            INSERT INTO task_q_dlq SELECT * FROM moved_task;
        """
        cursor.execute(query, (task_id,))
    print(f"Moved task {task_id} to DLQ with reason: {reason}")

def process_and_insert_silver(conn, task_payload: dict):
    """(AC5) Inserts validated data into the appropriate Silver table."""
    # This function would contain more specific logic based on task 'kind'
    # For now, we'll just print a success message.
    print(f"Processing and inserting task payload into Silver layer: {task_payload}")
    # Example for TA data:
    # with conn.cursor() as cursor:
    #     query = """INSERT INTO ta_silver (...) VALUES (...) ON CONFLICT (...) DO UPDATE ..."""
    #     cursor.execute(query, (...))
    pass

def process_tasks(conn):
    """Fetches and processes tasks in a loop."""
    while True:
        with conn.cursor() as cursor:
            # (AC1) Fetch one task using FOR UPDATE SKIP LOCKED
            query = "SELECT id, payload FROM task_q ORDER BY next_run_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED;"
            cursor.execute(query)
            task = cursor.fetchone()

            if not task:
                print("No tasks to process. Exiting.")
                break

            task_id, payload = task
            print(f"Processing task {task_id}...")

            if apply_sanity_rules(payload):
                process_and_insert_silver(conn, payload)
                # If successful, delete from the main queue
                cursor.execute("DELETE FROM task_q WHERE id = %s;", (task_id,))
            else:
                move_task_to_dlq(conn, task_id, 'sanity_fail')

            # Commit the transaction for this one task
            conn.commit()

def main():
    """Main entrypoint for the Silver layer worker."""
    print("Worker starting...")
    conn = None
    try:
        conn = get_db_connection()

        # (AC2) Acquire advisory lock
        if not get_advisory_lock(conn, ADVISORY_LOCK_NAME):
            # Exit gracefully if another worker is already running
            return

        check_backpressure(conn)
        process_tasks(conn)

    except Exception as e:
        print(f"An error occurred in the worker: {e}")
    finally:
        if conn:
            conn.close()
            print("Worker finished.")

if __name__ == "__main__":
    main()
