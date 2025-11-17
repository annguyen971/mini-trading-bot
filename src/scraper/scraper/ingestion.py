import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict

from core_lib.db import get_db_connection
from core_lib.queue import enqueue_task

def _calculate_content_hash(data: Dict[str, Any]) -> str:
    """Calculates a SHA256 hash of the JSON-serialized data."""
    # FIX 1: Thêm default=str để xử lý datetime (Đã làm ở bước trước)
    serialized_data = json.dumps(data, sort_keys=True, default=str).encode('utf-8')
    return hashlib.sha256(serialized_data).hexdigest()

def _normalize_to_utc(dt: datetime) -> datetime:
    """Ensures a datetime object is timezone-aware and in UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def process_and_ingest_data(raw_data: Dict[str, Any], source_name: str, task_kind: str):
    """
    Main workflow to process and ingest a single piece of raw data using core_lib.
    """
    conn = None
    try:
        content_hash = _calculate_content_hash(raw_data)

        first_seen_time = datetime.now(timezone.utc)
        publisher_time_raw = raw_data.get('publisher_time', first_seen_time)
        
        if isinstance(publisher_time_raw, str):
             publisher_time = first_seen_time
        else:
             publisher_time = _normalize_to_utc(publisher_time_raw)

        as_of_time = max(publisher_time, first_seen_time)

        conn = get_db_connection()

        with conn.cursor() as cursor:
            insert_query = """
                INSERT INTO raw_bronze (source_name, payload_json, content_hash, publisher_time, first_seen_time, as_of_time)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (content_hash) DO NOTHING
                RETURNING id;
            """
            
            # FIX 1: Thêm default=str vào json.dumps
            params = (
                source_name, 
                json.dumps(raw_data, default=str), 
                content_hash,
                publisher_time, first_seen_time, as_of_time
            )
            cursor.execute(insert_query, params)
            result = cursor.fetchone()

            if result:
                inserted_id = result[0]
                print(f"Successfully inserted record into raw_bronze with id: {inserted_id}")
                
                # ---> FIX 2: CHUYỂN UUID THÀNH STRING TẠI ĐÂY <---
                task_payload = {"bronze_id": str(inserted_id), "source": source_name}
                
                enqueue_task(conn, task_kind, task_payload)
            else:
                print(f"Record with hash {content_hash} already exists. Skipping enqueue.")

        conn.commit()

    except Exception as e:
        print(f"An error occurred during ingestion: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()