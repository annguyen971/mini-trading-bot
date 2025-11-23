from typing import Any

def get_advisory_lock(conn: Any, lock_name: str) -> bool:
    """
    Attempts to acquire a non-blocking advisory lock using the provided
    database connection.

    This uses a hash of the lock name to create a unique integer lock key.

    Args:
        conn: An active database connection object from psycopg.
        lock_name: A unique name for the lock to be acquired.

    Returns:
        True if the lock was acquired successfully, False otherwise.
    """
    try:
        # PostgreSQL advisory locks work with integers. We'll hash the name
        # to get a consistent integer key. A simple hash is sufficient.
        lock_key = hash(lock_name) & ((1 << 31) - 1) # Ensure it fits in a signed 32-bit int

        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
            lock_acquired = cursor.fetchone()[0]

            if lock_acquired:
                print(f"Successfully acquired advisory lock: '{lock_name}'")
            else:
                print(f"Failed to acquire advisory lock: '{lock_name}'. Another process may be running.")

            return lock_acquired
    except Exception as e:
        print(f"Error acquiring advisory lock '{lock_name}': {e}")
        return False

# Alias for compatibility
try_lock = get_advisory_lock
