import os
import psycopg

DB_URL = os.getenv("DB_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

def apply_migration():
    print("Applying migration 020...")
    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                with open("db/migrations/020_update_daily_sector_prices.sql", "r") as f:
                    sql = f.read()
                    cur.execute(sql)
            conn.commit()
            print("Migration 020 applied successfully.")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    apply_migration()
