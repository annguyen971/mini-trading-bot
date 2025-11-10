from typing import Any

def try_lock(conn: Any, lock_name: str) -> bool:
    """
    (AC2) Thử lấy advisory lock
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (lock_name,))
        ok = cur.fetchone()[0]
        if not ok:
            conn.rollback() # Không cần commit nếu chỉ SELECT
        return ok
