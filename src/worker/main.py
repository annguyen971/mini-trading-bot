import json, os, sys, re
from contextlib import contextmanager
import psycopg
from core_lib.locks import try_lock

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))
BACKLOG_THRESHOLD = int(os.getenv("BACKLOG_THRESHOLD", "10000"))

@contextmanager
def pg_conn():
    # Kết nối tới DB
    with psycopg.connect(os.getenv("PG_DSN")) as conn:
        conn.autocommit = False
        yield conn

def fetch_batch(conn):
    # (AC1) Lấy batch mới bằng FOR UPDATE SKIP LOCKED
    with conn.cursor() as cur:
        cur.execute("""
            WITH cte AS (
                SELECT id FROM task_q
                WHERE status='ready'
                ORDER BY priority, first_seen
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            SELECT t.id, t.task_type, t.payload
            FROM task_q t JOIN cte ON t.id = cte.id
        """, (BATCH_SIZE,))
        return cur.fetchall()

def apply_sanity_rules_ta(p):
    # (AC3) Triển khai các rule TA trong Mục 3.1
    if not re.match(r"^[A-Z]{3,7}$", p.get("symbol", "")):
        return False, "symbol_format", "Invalid symbol format"
    if not p.get("trade_date"):
        return False, "trade_date_null", "Trade date is null"
    # Additional date validation can be added here
    for price in ["open", "high", "low", "close"]:
        if p.get(price) is None or p.get(price) < 0:
            return False, f"{price}_invalid", f"{price} is invalid"
    if p.get("high") < max(p.get("open"), p.get("close")):
        return False, "high_price_invalid", "High price is invalid"
    if p.get("low") > min(p.get("open"), p.get("close")):
        return False, "low_price_invalid", "Low price is invalid"
    if p.get("high") < p.get("low"):
        return False, "high_low_invalid", "High is less than low"
    if p.get("volume") is None or p.get("volume") < 0:
        return False, "volume_invalid", "Volume is invalid"
    if p.get("volume") == 0 and not (p.get("open") == p.get("high") == p.get("low") == p.get("close")):
        return False, "volume_zero_prices_not_equal", "Volume is zero but prices are not equal"
    if not p.get("currency"):
        return False, "currency_null", "Currency is null"
    return True, None, None

def apply_sanity_rules_sa(p):
    # (AC3) Triển khai các rule SA trong Mục 3.2
    if not p.get("url_canonical") or not re.match(r"^https?://", p.get("url_canonical", "")):
        return False, "url_invalid", "Invalid URL"
    if not p.get("publisher_time"):
        return False, "publisher_time_null", "Publisher time is null"
    # Additional timestamp logic can be added here
    if p.get("language") not in ["vi", "en"]:
        p["language"] = "unknown"
    if not p.get("text_normalized") or len(p.get("text_normalized")) < 120:
        return False, "text_too_short", "Text is too short"
    if not p.get("content_hash"):
        return False, "content_hash_null", "Content hash is null"
    if p.get("symbols"):
        for symbol in p.get("symbols"):
            if not re.match(r"^[A-Z]{3,7}$", symbol):
                return False, "symbol_format_invalid", "Invalid symbol format in symbols list"
    if not p.get("source_domain"):
        return False, "source_domain_null", "Source domain is null"
    return True, None, None

def upsert_ta(conn, p):
    # (AC5) Triển khai logic INSERT ... ON CONFLICT (Mục 4.1)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO ta_silver (symbol, trade_date, open, high, low, close, volume, vwap, adj_close, currency, price_multiplier, source, first_seen_time, content_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, trade_date)
            DO UPDATE SET
              open = EXCLUDED.open,
              high = EXCLUDED.high,
              low = EXCLUDED.low,
              close = EXCLUDED.close,
              volume = EXCLUDED.volume,
              vwap = EXCLUDED.vwap,
              adj_close = EXCLUDED.adj_close,
              source = EXCLUDED.source,
              first_seen_time = LEAST(ta_silver.first_seen_time, EXCLUDED.first_seen_time),
              ingest_time = now(),
              content_hash = EXCLUDED.content_hash;
        """, (
            p.get("symbol"), p.get("trade_date"), p.get("open"), p.get("high"), p.get("low"), p.get("close"), p.get("volume"),
            p.get("vwap"), p.get("adj_close"), p.get("currency"), p.get("price_multiplier"), p.get("source"),
            p.get("first_seen_time"), p.get("content_hash")
        ))

def upsert_sa(conn, p):
    # (AC5) Triển khai logic INSERT ... ON CONFLICT (Mục 4.2)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sa_silver (url_canonical, source_domain, publisher_time, first_seen_time, language, title, text_normalized, content_hash, symbols, author, topic_tags, hype_raw, hype_crowd, hype_elitist, account_weights_applied)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url_canonical)
            DO UPDATE SET
              publisher_time = LEAST(sa_silver.publisher_time, EXCLUDED.publisher_time),
              first_seen_time = LEAST(sa_silver.first_seen_time, EXCLUDED.first_seen_time),
              language = COALESCE(EXCLUDED.language, sa_silver.language),
              title = COALESCE(EXCLUDED.title, sa_silver.title),
              text_normalized = COALESCE(EXCLUDED.text_normalized, sa_silver.text_normalized),
              symbols = CASE WHEN array_length(EXCLUDED.symbols, 1) IS NOT NULL
                             THEN EXCLUDED.symbols ELSE sa_silver.symbols END,
              hype_raw = COALESCE(EXCLUDED.hype_raw, sa_silver.hype_raw),
              hype_crowd = COALESCE(EXCLUDED.hype_crowd, sa_silver.hype_crowd),
              hype_elitist = COALESCE(EXCLUDED.hype_elitist, sa_silver.hype_elitist),
              content_hash = EXCLUDED.content_hash,
              ingest_time = now();
        """, (
            p.get("url_canonical"), p.get("source_domain"), p.get("publisher_time"), p.get("first_seen_time"),
            p.get("language"), p.get("title"), p.get("text_normalized"), p.get("content_hash"), p.get("symbols"),
            p.get("author"), p.get("topic_tags"), p.get("hype_raw"), p.get("hype_crowd"), p.get("hype_elitist"),
            p.get("account_weights_applied")
        ))

def move_to_dlq(conn, task_id, payload, reason, rule_id=None, error_msg=None):
    # (AC4) Ghi vào DLQ và cập nhật status task_q
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO task_q_dlq(task_id, reason, rule_id, payload, error_msg, created_at)
            VALUES (%s, %s, %s, %s::jsonb, %s, NOW())
        """, (task_id, reason, rule_id, json.dumps(payload), error_msg))
        cur.execute("UPDATE task_q SET status='dlq', last_attempt=now() WHERE id=%s", (task_id,))

def mark_done(conn, task_id):
    # Xóa task đã xử lý thành công khỏi hàng đợi
    with conn.cursor() as cur:
        cur.execute("DELETE FROM task_q WHERE id=%s", (task_id,))

def backlog_control(conn):
    # (AC7) Kiểm soát cờ SCRAPE_SLOW
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM task_q WHERE status='ready'")
        n = cur.fetchone()[0]
        val = (n > BACKLOG_THRESHOLD)
        cur.execute("""
            INSERT INTO control_flags(name, value, updated_at) VALUES ('SCRAPE_SLOW', %s, NOW())
            ON CONFLICT (name) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()
        """, (val,))

def main():
    lock_name = 'silver_consume'
    with pg_conn() as conn:
        if not try_lock(conn, lock_name):
            print(f"Lock '{lock_name}' đang bận; worker thoát 0.")
            return 0

        try:
            tasks = fetch_batch(conn)
            if not tasks:
                print("Không có task nào, worker kết thúc.")
                conn.commit() # Vẫn commit để backlog_control chạy

            for task_id, task_type, payload in tasks:
                p = payload if isinstance(payload, dict) else json.loads(payload)
                try:
                    if task_type == 'ta.bar':
                        ok, rid, msg = apply_sanity_rules_ta(p)
                        if not ok:
                            move_to_dlq(conn, task_id, p, 'sanity_fail', rid, msg)
                            continue
                        upsert_ta(conn, p)
                        mark_done(conn, task_id)

                    elif task_type == 'sa.article':
                        ok, rid, msg = apply_sanity_rules_sa(p)
                        if not ok:
                            move_to_dlq(conn, task_id, p, 'sanity_fail', rid, msg)
                            continue
                        upsert_sa(conn, p)
                        mark_done(conn, task_id)

                    else:
                        move_to_dlq(conn, task_id, p, 'unknown_task_type')

                except Exception as e:
                    # Bắt lỗi trong vòng lặp để không làm hỏng toàn bộ batch
                    print(f"Lỗi xử lý task {task_id}: {e}")
                    move_to_dlq(conn, task_id, p, 'exception', error_msg=str(e))

            # (AC7) Luôn chạy backlog control sau mỗi batch
            backlog_control(conn)

            # Commit toàn bộ batch
            conn.commit()

        except Exception as e:
            print(f"Lỗi nghiêm trọng, rollback batch: {e}")
            conn.rollback()
        finally:
            # Luôn giải phóng lock
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))
            conn.commit() # Commit việc giải phóng lock

    return 0

if __name__ == '__main__':
    sys.exit(main())
