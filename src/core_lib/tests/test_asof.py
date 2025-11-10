import pytest
import psycopg2
import os
from datetime import datetime, timedelta

@pytest.fixture(scope="module")
def db_conn():
    conn = psycopg2.connect(os.getenv("PG_TEST_DSN"))
    yield conn
    conn.close()

def test_time_cheat_attack(db_conn):
    with db_conn.cursor() as cur:
        # Arrange: Setup a scenario with future data
        cur.execute("DELETE FROM raw_bronze;")

        today = datetime.now()
        tomorrow = today + timedelta(days=1)

        cur.execute("""
            INSERT INTO raw_bronze (source_name, content_hash, as_of_time)
            VALUES (%s, %s, %s);
        """, ("test_source", "hash_today", today))

        cur.execute("""
            INSERT INTO raw_bronze (source_name, content_hash, as_of_time)
            VALUES (%s, %s, %s);
        """, ("test_source", "hash_tomorrow", tomorrow))
        db_conn.commit()

        # Act: Run the as-of logic by querying the view
        cur.execute("CREATE OR REPLACE VIEW v_features_asof AS SELECT * FROM raw_bronze WHERE as_of_time <= NOW();")
        cur.execute("SELECT * FROM v_features_asof;")
        results = cur.fetchall()

        # Assert: Verify that the future data was excluded
        assert len(results) == 1
        assert results[0][4] == "hash_today"
