import json
from typing import Any, Dict

def enqueue_task(conn: Any, kind: str, payload: Dict[str, Any]):
    """
    Enqueues a new task into the task_q table using the provided
    database connection.

    Args:
        conn: An active database connection object from psycopg.
        kind: The kind of task to enqueue (e.g., 'process_sa_data').
        payload: A JSON-serializable dictionary with the task details.
    """
    try:
        with conn.cursor() as cursor:
            insert_query = """
                INSERT INTO task_q (kind, payload)
                VALUES (%s, %s);
            """
            cursor.execute(insert_query, (kind, json.dumps(payload)))
        print(f"Successfully enqueued task of kind '{kind}'.")
    except Exception as e:
        print(f"Error enqueuing task: {e}")
        # The calling function is responsible for transaction management (commit/rollback)
        raise
