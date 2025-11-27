import os
import sys

# Add src to path to import core_lib
sys.path.append(os.path.join(os.path.dirname(__file__), '../src/core_lib'))

from core_lib.db import get_db_connection

def apply_migration():
    migration_file = os.path.join(os.path.dirname(__file__), '../db/migrations/018_update_v_features_asof.sql')
    
    with open(migration_file, 'r') as f:
        sql = f.read()
        
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            print(f"Applying migration: {migration_file}")
            cur.execute(sql)
            conn.commit()
            print("Migration applied successfully.")
    except Exception as e:
        print(f"Error applying migration: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    apply_migration()
