import pytest
import psycopg2
import os
import json
from src.worker.main import main as process_tasks

@pytest.fixture(scope="module")
def db_conn():
    conn = psycopg2.connect(os.getenv("PG_TEST_DSN"))
    yield conn
    conn.close()

def test_dlq_mechanism(db_conn):
    with db_conn.cursor() as cur:
        # Arrange: Insert a malformed task into task_q
        cur.execute("DELETE FROM task_q;")
        cur.execute("DELETE FROM task_q_dlq;")

        malformed_payload = json.dumps({"symbol": "INVALID-SYMBOL"})
        cur.execute("""
            INSERT INTO task_q (task_type, payload)
            VALUES (%s, %s) RETURNING id;
        """, ("ta.bar", malformed_payload))
        task_id = cur.fetchone()[0]
        db_conn.commit()

        # Act: Run the worker process
        process_tasks()

        # Assert: Verify the task is moved to task_q_dlq
        cur.execute("SELECT * FROM task_q WHERE id = %s;", (task_id,))
        assert cur.fetchone() is None

        cur.execute("SELECT * FROM task_q_dlq WHERE task_id = %s;", (task_id,))
        dlq_entry = cur.fetchone()
        assert dlq_entry is not None
        assert dlq_entry[2] == 'sanity_fail'
