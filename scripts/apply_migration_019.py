import os
import psycopg

DB_URL = os.getenv("DB_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

def apply_migration():
    print("Applying migration 019...")
    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                # Ensure schema_migrations table exists
                cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    description TEXT,
                    applied_at TIMESTAMPTZ DEFAULT NOW()
                );
                """)
                
                with open("db/migrations/019_add_market_xray_metrics.sql", "r") as f:
                    sql = f.read()
                    cur.execute(sql)
            conn.commit()
            print("Migration 019 applied successfully.")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    apply_migration()
