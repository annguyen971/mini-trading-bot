"""
Silver Worker - Stock Hunter AI
================================
Worker xử lý Bronze → Silver transformation với sanity checks.

Story 1.3: Đọc task_q bằng FOR UPDATE SKIP LOCKED, áp dụng cheap sanity rules,
UPSERT vào ta_silver/sa_silver, hoặc đẩy sang DLQ.

Reference: doc/silver_logic_v1.md
"""

import json
import os
import re
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional, List
from urllib.parse import urlparse

from core_lib.db import get_db_connection
from core_lib.locks import get_advisory_lock

# --- Constants ---
BATCH_SIZE = int(os.getenv("SILVER_BATCH_SIZE", "500"))
BACKPRESSURE_THRESHOLD = int(os.getenv("BACKPRESSURE_THRESHOLD", "10000"))
ADVISORY_LOCK_NAME = "silver_consume"

# Regex patterns
SYMBOL_PATTERN = re.compile(r'^[A-Z]{3,7}$')
URL_PATTERN = re.compile(r'^https?://')

# --- Advisory Lock Helper ---

def try_lock(conn, lock_name: str) -> bool:
    """
    (AC2) Tries to acquire PostgreSQL advisory lock.
    Returns True if successful, False if lock is already held.
    """
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (lock_name,))
        acquired = cursor.fetchone()[0]
        if not acquired:
            print(f"Lock '{lock_name}' is already held by another worker. Exiting gracefully.")
        return acquired

def release_lock(conn, lock_name: str):
    """Releases the advisory lock."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
        print(f"Released lock '{lock_name}'.")

# --- Batch Fetching ---

def fetch_batch(conn, batch_size: int = BATCH_SIZE) -> List[Tuple]:
    """
    (AC1) Fetches a batch of tasks from task_q using FOR UPDATE SKIP LOCKED.
    Returns list of (id, kind, payload) tuples.
    """
    with conn.cursor() as cursor:
        query = """
            WITH cte AS (
                SELECT id FROM task_q
                WHERE next_run_at <= NOW()
                  AND retry_count < max_retries
                ORDER BY next_run_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            SELECT t.id, t.kind, t.payload
            FROM task_q t
            JOIN cte ON t.id = cte.id
        """
        cursor.execute(query, (batch_size,))
        tasks = cursor.fetchall()
        print(f"Fetched {len(tasks)} tasks from task_q.")
        return tasks

# --- TA Sanity Rules (7 rules from silver_logic_v1.md Section 3.1) ---

def apply_ta_sanity_rules(payload: dict) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    (AC3) Applies 7 cheap sanity rules for Technical Analysis data.

    Returns:
        (True, None, None) if all rules pass
        (False, rule_id, error_message) if any rule fails
    """

    # Rule 1: Symbol validation
    symbol = payload.get('symbol', '')
    if not symbol or not SYMBOL_PATTERN.match(symbol):
        return False, 'ta_rule_1_symbol', f'Invalid symbol format: {symbol}'

    # Rule 2: Trade date validation
    trade_date_str = payload.get('trade_date') or payload.get('time')
    if not trade_date_str:
        return False, 'ta_rule_2_date_missing', 'Trade date is missing'

    try:
        trade_date = datetime.strptime(trade_date_str, '%Y-%m-%d').date()
        if trade_date > datetime.now().date():
            return False, 'ta_rule_2_date_future', f'Trade date in future: {trade_date}'
    except (ValueError, TypeError) as e:
        return False, 'ta_rule_2_date_invalid', f'Invalid date format: {trade_date_str}'

    # Rule 3: Price validation (not NULL, >= 0)
    try:
        open_price = float(payload.get('open', -1))
        high_price = float(payload.get('high', -1))
        low_price = float(payload.get('low', -1))
        close_price = float(payload.get('close', -1))

        if any(p < 0 for p in [open_price, high_price, low_price, close_price]):
            return False, 'ta_rule_3_price_negative', 'One or more prices are negative or missing'
    except (ValueError, TypeError):
        return False, 'ta_rule_3_price_invalid', 'Price values are not numeric'

    # Rule 4: Price relationship validation
    if high_price < max(open_price, close_price):
        return False, 'ta_rule_4_high_invalid', f'High {high_price} < max(open, close) {max(open_price, close_price)}'

    if low_price > min(open_price, close_price):
        return False, 'ta_rule_4_low_invalid', f'Low {low_price} > min(open, close) {min(open_price, close_price)}'

    if high_price < low_price:
        return False, 'ta_rule_4_high_low_invalid', f'High {high_price} < Low {low_price}'

    # Rule 5: Volume validation (not NULL, >= 0)
    try:
        volume = int(payload.get('volume', -1))
        if volume < 0:
            return False, 'ta_rule_5_volume_negative', f'Volume is negative: {volume}'
    except (ValueError, TypeError):
        return False, 'ta_rule_5_volume_invalid', 'Volume is not numeric'

    # Rule 6: Business rule (volume == 0 means doji candle)
    if volume == 0:
        if not (open_price == high_price == low_price == close_price):
            return False, 'ta_rule_6_zero_volume_not_doji', \
                f'Volume=0 but OHLC not equal: {open_price}, {high_price}, {low_price}, {close_price}'

    # Rule 7: Currency (required or default to VND)
    currency = payload.get('currency', 'VND')
    if not currency:
        return False, 'ta_rule_7_currency_missing', 'Currency is required'

    # All rules passed
    return True, None, None

# --- SA Sanity Rules (8 rules from silver_logic_v1.md Section 3.2) ---

def apply_sa_sanity_rules(payload: dict) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    (AC3) Applies 8 cheap sanity rules for Sentiment Analysis data.

    Returns:
        (True, None, None) if all rules pass
        (False, rule_id, error_message) if any rule fails
    """

    # Rule 1: URL validation
    url = payload.get('url_canonical', '') or payload.get('url', '')
    if not url or not URL_PATTERN.match(url):
        return False, 'sa_rule_1_url_invalid', f'Invalid or missing URL: {url}'

    # Rule 2: Timestamp validation (not NULL, not > now + 5 min)
    publisher_time_str = payload.get('publisher_time_utc') or payload.get('publisher_time')
    if not publisher_time_str:
        return False, 'sa_rule_2_timestamp_missing', 'Publisher timestamp is missing'

    try:
        publisher_time = datetime.fromisoformat(publisher_time_str.replace('Z', '+00:00'))
        max_future = datetime.now() + timedelta(minutes=5)
        if publisher_time > max_future:
            return False, 'sa_rule_2_timestamp_future', \
                f'Publisher time too far in future: {publisher_time}'
    except (ValueError, TypeError) as e:
        return False, 'sa_rule_2_timestamp_invalid', f'Invalid timestamp format: {publisher_time_str}'

    # Rule 3: Timestamp logic (first_seen >= publisher_time - 1 day)
    first_seen_str = payload.get('first_seen_time')
    if first_seen_str:
        try:
            first_seen = datetime.fromisoformat(first_seen_str.replace('Z', '+00:00'))
            min_valid = publisher_time - timedelta(days=1)
            if first_seen < min_valid:
                return False, 'sa_rule_3_timestamp_logic', \
                    f'first_seen {first_seen} < publisher_time - 1 day {min_valid}'
        except (ValueError, TypeError):
            pass  # If first_seen is invalid, we skip this check

    # Rule 4: Language validation
    language = payload.get('language', 'unknown')
    if language not in ['vi', 'en', 'unknown']:
        # Auto-fix: set to unknown if invalid
        payload['language'] = 'unknown'
        print(f"WARN: Invalid language '{language}', set to 'unknown'")

    # Rule 5: Content validation (text >= 120 chars)
    text_normalized = payload.get('text_normalized', '') or payload.get('text', '')
    if len(text_normalized) < 120:
        return False, 'sa_rule_5_text_too_short', \
            f'Text length {len(text_normalized)} < 120 chars (after HTML removal)'

    # Rule 6: Hash validation
    content_hash = payload.get('content_hash', '') or payload.get('text_norm_hash', '')
    if not content_hash:
        return False, 'sa_rule_6_hash_missing', 'Content hash is required for deduplication'

    # Rule 7: Symbols validation (if present, each must match pattern)
    symbols = payload.get('symbols', []) or payload.get('entities', [])
    if symbols:
        for sym in symbols:
            if not SYMBOL_PATTERN.match(sym):
                return False, 'sa_rule_7_symbol_invalid', f'Invalid symbol in array: {sym}'

    # Rule 8: Domain validation
    source_domain = payload.get('source_domain', '') or payload.get('source_name', '')
    if not source_domain:
        # Try to extract from URL
        try:
            parsed = urlparse(url)
            source_domain = parsed.netloc
            payload['source_domain'] = source_domain
        except Exception:
            return False, 'sa_rule_8_domain_missing', 'Source domain is required'

    # All rules passed
    return True, None, None

# --- UPSERT Logic (AC5) ---

def upsert_ta_silver(conn, payload: dict):
    """
    (AC5) Inserts or updates TA data into ta_silver table.
    Logic: Overwrite with latest data, but keep earliest first_seen_time (audit).
    """
    with conn.cursor() as cursor:
        # Map payload keys to match ta_silver schema
        symbol = payload['symbol']
        trade_date = payload.get('trade_date') or payload.get('time')
        open_price = float(payload['open'])
        high = float(payload['high'])
        low = float(payload['low'])
        close = float(payload['close'])
        volume = int(payload['volume'])

        # Optional fields
        turnover = payload.get('turnover')
        as_of_time = payload.get('as_of_time', datetime.now())
        bronze_ref_id = payload.get('bronze_ref_id')

        query = """
            INSERT INTO ta_silver (
                symbol, trade_date, open, high, low, close, volume,
                turnover, as_of_time, bronze_ref_id, validated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, NOW()
            )
            ON CONFLICT (symbol, trade_date)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                turnover = EXCLUDED.turnover,
                as_of_time = LEAST(ta_silver.as_of_time, EXCLUDED.as_of_time),
                bronze_ref_id = COALESCE(EXCLUDED.bronze_ref_id, ta_silver.bronze_ref_id),
                validated_at = NOW()
        """

        cursor.execute(query, (
            symbol, trade_date, open_price, high, low, close, volume,
            turnover, as_of_time, bronze_ref_id
        ))
        print(f"Upserted TA data for {symbol} on {trade_date}")

def upsert_sa_silver(conn, payload: dict):
    """
    (AC5) Inserts or updates SA data into sa_silver table.
    Logic: Enrichment - use COALESCE to only fill NULL fields, don't overwrite.
    """
    with conn.cursor() as cursor:
        # Map payload keys to match sa_silver schema
        url_canonical = payload.get('url_canonical') or payload.get('url')
        source_name = payload.get('source_domain') or payload.get('source_name')
        publisher_time_utc = payload.get('publisher_time_utc') or payload.get('publisher_time')
        as_of_time = payload.get('as_of_time', datetime.now())
        text_norm_hash = payload.get('content_hash') or payload.get('text_norm_hash')
        text_len = len(payload.get('text_normalized', '') or payload.get('text', ''))
        language = payload.get('language', 'unknown')
        bronze_ref_id = payload.get('bronze_ref_id')
        sentiment_score = payload.get('sentiment_score')

        query = """
            INSERT INTO sa_silver (
                url_canonical, source_name, publisher_time_utc, as_of_time,
                text_norm_hash, text_len, language, bronze_ref_id,
                sentiment_score, validated_at
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, NOW()
            )
            ON CONFLICT (url_canonical)
            DO UPDATE SET
                -- Audit: Keep earliest timestamps
                publisher_time_utc = LEAST(sa_silver.publisher_time_utc, EXCLUDED.publisher_time_utc),
                as_of_time = LEAST(sa_silver.as_of_time, EXCLUDED.as_of_time),

                -- Enrichment: Only update if current value is NULL
                language = COALESCE(EXCLUDED.language, sa_silver.language),
                text_norm_hash = COALESCE(EXCLUDED.text_norm_hash, sa_silver.text_norm_hash),
                text_len = COALESCE(EXCLUDED.text_len, sa_silver.text_len),
                sentiment_score = COALESCE(EXCLUDED.sentiment_score, sa_silver.sentiment_score),
                bronze_ref_id = COALESCE(EXCLUDED.bronze_ref_id, sa_silver.bronze_ref_id),

                validated_at = NOW()
        """

        cursor.execute(query, (
            url_canonical, source_name, publisher_time_utc, as_of_time,
            text_norm_hash, text_len, language, bronze_ref_id,
            sentiment_score
        ))
        print(f"Upserted SA data for {url_canonical}")

# --- DLQ Handling (AC4) ---

def move_to_dlq(conn, task_id: int, payload: dict, reason: str,
                rule_id: Optional[str] = None, error_msg: Optional[str] = None):
    """
    (AC4) Moves a failed task to the dead-letter queue.
    Also marks the task in task_q for auditing.
    """
    with conn.cursor() as cursor:
        # Insert into DLQ
        query_dlq = """
            INSERT INTO task_q_dlq (
                id, kind, payload, next_run_at, retry_count, max_retries,
                created_at, moved_at
            )
            SELECT id, kind, payload, next_run_at, retry_count, max_retries,
                   created_at, NOW()
            FROM task_q
            WHERE id = %s
        """
        cursor.execute(query_dlq, (task_id,))

        # Delete from main queue
        cursor.execute("DELETE FROM task_q WHERE id = %s", (task_id,))

        print(f"Moved task {task_id} to DLQ. Reason: {reason}, Rule: {rule_id}, Error: {error_msg}")

def mark_done(conn, task_id: int):
    """Deletes successfully processed task from task_q."""
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM task_q WHERE id = %s", (task_id,))

# --- Backpressure Control (AC7) ---

def check_and_set_backpressure(conn, threshold: int = BACKPRESSURE_THRESHOLD):
    """
    (AC7) Checks task_q backlog and sets SCRAPE_SLOW flag if > threshold.
    This signals to scraper to slow down.
    """
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM task_q WHERE next_run_at <= NOW()")
        backlog = cursor.fetchone()[0]

        is_slow = backlog > threshold

        query = """
            INSERT INTO control_flags (flag, enabled, reason, updated_at)
            VALUES ('SCRAPE_SLOW', %s, %s, NOW())
            ON CONFLICT (flag)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                reason = EXCLUDED.reason,
                updated_at = NOW()
        """

        reason = f'Backlog: {backlog} tasks (threshold: {threshold})' if is_slow else 'Backlog cleared'
        cursor.execute(query, (is_slow, reason))

        if is_slow:
            print(f"⚠️  BACKPRESSURE ENABLED: {backlog} tasks > {threshold}")
        else:
            print(f"✓ Backpressure cleared: {backlog} tasks")

# --- Main Worker Loop ---

def process_batch(conn, batch_size: int = BATCH_SIZE):
    """
    Main batch processing logic:
    1. Fetch batch with FOR UPDATE SKIP LOCKED
    2. Apply sanity rules
    3. UPSERT to Silver or move to DLQ
    4. Check backpressure
    """
    tasks = fetch_batch(conn, batch_size)

    if not tasks:
        print("No tasks to process. Worker idle.")
        return False  # No work done

    success_count = 0
    dlq_count = 0

    for task_id, kind, payload_raw in tasks:
        try:
            # Parse payload
            payload = payload_raw if isinstance(payload_raw, dict) else json.loads(payload_raw)

            # Route by task kind
            if kind == 'TA_PROCESS':
                # Apply TA sanity rules
                passed, rule_id, error_msg = apply_ta_sanity_rules(payload)

                if not passed:
                    move_to_dlq(conn, task_id, payload, 'sanity_fail', rule_id, error_msg)
                    dlq_count += 1
                    continue

                # UPSERT to ta_silver
                upsert_ta_silver(conn, payload)
                mark_done(conn, task_id)
                success_count += 1

            elif kind == 'NLP_PROCESS':
                # Apply SA sanity rules
                passed, rule_id, error_msg = apply_sa_sanity_rules(payload)

                if not passed:
                    move_to_dlq(conn, task_id, payload, 'sanity_fail', rule_id, error_msg)
                    dlq_count += 1
                    continue

                # UPSERT to sa_silver
                upsert_sa_silver(conn, payload)
                mark_done(conn, task_id)
                success_count += 1

            else:
                # Unknown task kind
                move_to_dlq(conn, task_id, payload, 'unknown_kind', None, f'Unknown kind: {kind}')
                dlq_count += 1

        except Exception as e:
            # Catch errors for individual tasks to avoid batch failure
            print(f"ERROR processing task {task_id}: {e}")
            try:
                move_to_dlq(conn, task_id, payload, 'exception', None, str(e))
                dlq_count += 1
            except Exception as dlq_error:
                print(f"CRITICAL: Failed to move task {task_id} to DLQ: {dlq_error}")
                conn.rollback()
                raise

    # Check backpressure after batch
    check_and_set_backpressure(conn)

    # Commit entire batch
    conn.commit()

    print(f"Batch complete: {success_count} success, {dlq_count} to DLQ")
    return True  # Work was done

# --- Main Entry Point ---

def main():
    """
    Main entrypoint for Silver Worker.

    Execution flow:
    1. Acquire advisory lock (AC2)
    2. Process batches in loop (AC1)
    3. Apply sanity rules (AC3)
    4. UPSERT or DLQ (AC4, AC5)
    5. Check backpressure (AC7)
    6. Release lock
    """
    print("=" * 60)
    print("Silver Worker starting...")
    print(f"Batch size: {BATCH_SIZE}, Backpressure threshold: {BACKPRESSURE_THRESHOLD}")
    print("=" * 60)

    conn = None

    try:
        conn = get_db_connection()

        # (AC2) Try to acquire advisory lock
        if not try_lock(conn, ADVISORY_LOCK_NAME):
            print("Another worker is already running. Exiting gracefully.")
            return 0

        print(f"✓ Acquired lock '{ADVISORY_LOCK_NAME}'")

        # Process batches until no more work
        batch_count = 0
        while True:
            print(f"\n--- Batch {batch_count + 1} ---")
            work_done = process_batch(conn, BATCH_SIZE)

            if not work_done:
                print("No more work. Worker finishing.")
                break

            batch_count += 1

            # Safety limit: prevent infinite loop
            if batch_count >= 100:
                print("WARN: Processed 100 batches, stopping to prevent infinite loop.")
                break

        print(f"\n✓ Processed {batch_count} batches total")

    except Exception as e:
        print(f"ERROR: Worker failed with exception: {e}")
        if conn:
            conn.rollback()
        return 1

    finally:
        if conn:
            try:
                release_lock(conn, ADVISORY_LOCK_NAME)
                conn.commit()
            except Exception as e:
                print(f"WARN: Failed to release lock: {e}")
            finally:
                conn.close()

        print("=" * 60)
        print("Silver Worker finished.")
        print("=" * 60)

    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
