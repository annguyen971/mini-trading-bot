import os
import sys
import psycopg

# List of essential tables that must exist for the system to be considered healthy.
TABLES_TO_CHECK = [
    'task_q',
    'raw_bronze',
    'sa_silver',
    'scraper_state',       # New in V3.1
    'raw_bronze_bad',      # New in V3.1
    'model_registry'
]

def main():
    """
    Connects to the database using the DB_URL from the environment and verifies
    that the schema (key tables and columns) has been initialized correctly.
    """
    print("🚀 Starting Infrastructure Verification Script...")

    db_url = os.getenv("DB_URL")
    if not db_url:
        print("\n❌ FATAL: DB_URL not found in environment variables.")
        print("   Please ensure the .env file is created and sourced, or that the environment variable is set.")
        sys.exit(1)

    print(f"Found DB_URL, attempting to connect...")

    try:
        with psycopg.connect(db_url) as conn:
            print("✅ Database connection successful.")
            print("\n🔍 Verifying schema: Checking for table existence...")

            with conn.cursor() as cur:
                missing_tables = []
                for table in TABLES_TO_CHECK:
                    # Using to_regclass is a safe and standard PostgreSQL way to check for table existence.
                    cur.execute("SELECT to_regclass('public.%s');", (table,))
                    if cur.fetchone()[0] is None:
                        print(f"  - ❌ Table '{table}' NOT found.")
                        missing_tables.append(table)
                    else:
                        print(f"  - ✅ Table '{table}' found.")

                if missing_tables:
                    print(f"\n❌ ERROR: {len(missing_tables)} required table(s) are missing from the database.")
                    sys.exit(1)

                print("  All required tables found.")

                # Specifically verify that the 'sentiment_score' column was added.
                print("\n🔍 Verifying schema: Checking for column existence...")
                cur.execute("""
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name='sa_silver' AND column_name='sentiment_score';
                """)
                if cur.fetchone() is None:
                    print("  - ❌ Column 'sentiment_score' in 'sa_silver' NOT found.")
                    sys.exit(1)
                else:
                    print("  - ✅ Column 'sentiment_score' in 'sa_silver' found.")

                print("  All required columns found.")

    except psycopg.OperationalError as e:
        print(f"\n❌ FATAL: Could not connect to the database.")
        print(f"   Error: {e}")
        print("   Please check if the database container is running and the DB_URL in your .env file is correct.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ FATAL: An unexpected error occurred: {e}")
        sys.exit(1)

    print("\n🎉 SUCCESS: All infrastructure checks passed!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
