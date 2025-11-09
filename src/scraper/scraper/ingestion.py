import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict

# --- Placeholders for core_lib imports ---
# In a real implementation, these would be:
# from core_lib.db import get_db_connection
# from core_lib.queue import enqueue_task

def get_db_connection():
    """Placeholder for getting a database connection."""
    print("Connecting to the database...")
    class MockConnection:
        def __init__(self):
            self._cursor = self.MockCursor()
        def cursor(self):
            return self._cursor
        def commit(self):
            print("DB TRANSACTION COMMITTED")
        def close(self):
            print("DB connection closed.")
        class MockCursor:
            def __init__(self):
                self.last_result = None
            def execute(self, query, params):
                print(f"Executing query: {query.strip()} with params: {params}")
                if "INSERT INTO raw_bronze" in query:
                    # Simulate returning a new ID for a successful insert
                    self.last_result = ('mock-uuid-1234',)
            def fetchone(self):
                return self.last_result
            def close(self):
                pass
    return MockConnection()

def enqueue_task(conn, kind: str, payload: Dict[str, Any]):
    """Placeholder for enqueuing a task using the same transaction."""
    print(f"Enqueuing task of kind '{kind}' with payload: {payload}")
    # This would use the connection/cursor from the calling function
    pass

# --- Core Ingestion Logic ---

def _calculate_content_hash(data: Dict[str, Any]) -> str:
    """Calculates a SHA256 hash of the JSON-serialized data."""
    serialized_data = json.dumps(data, sort_keys=True).encode('utf-8')
    return hashlib.sha256(serialized_data).hexdigest()

def _normalize_to_utc(dt: datetime) -> datetime:
    """(AC2) Ensures a datetime object is timezone-aware and in UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def process_and_ingest_data(raw_data: Dict[str, Any], source_name: str, task_kind: str):
    """
    Main workflow to process and ingest a single piece of raw data.
    Implements as-of, idempotency, and transactional logic.
    """
    conn = None
    try:
        # --- 1. Data Processing and As-of Logic ---
        content_hash = _calculate_content_hash(raw_data)

        first_seen_time = datetime.now(timezone.utc)
        publisher_time_raw = raw_data.get('publisher_time', first_seen_time)
        publisher_time = _normalize_to_utc(publisher_time_raw)

        # AC1: As-of time logic
        as_of_time = max(publisher_time, first_seen_time)

        # --- 2. Database Transaction ---
        conn = get_db_connection()
        cursor = conn.cursor()

        # AC4: Idempotent write to raw_bronze
        insert_query = """
            INSERT INTO raw_bronze (source_name, payload_json, content_hash, publisher_time, first_seen_time, as_of_time)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (content_hash) DO NOTHING
            RETURNING id;
        """
        params = (
            source_name, json.dumps(raw_data), content_hash,
            publisher_time, first_seen_time, as_of_time
        )
        cursor.execute(insert_query, params)
        result = cursor.fetchone()

        # AC6: Enqueue task only if the insert was successful
        if result:
            inserted_id = result[0]
            print(f"Successfully inserted record into raw_bronze with id: {inserted_id}")
            task_payload = {"bronze_id": inserted_id, "source": source_name}
            enqueue_task(conn, task_kind, task_payload)
        else:
            print(f"Record with hash {content_hash} already exists. Skipping enqueue.")

        conn.commit()

    except Exception as e:
        print(f"An error occurred during ingestion: {e}")
        # In a real app, you would rollback the transaction here: conn.rollback()
    finally:
        if conn:
            conn.close()
