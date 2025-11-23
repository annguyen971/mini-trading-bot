#!/usr/bin/env python3
"""
Manual Macro Data Update Script
Use this script to update monthly/quarterly macro indicators that are not available via free APIs.
Run: docker compose exec -T worker python /app/scripts/update_macro_manual.py
"""

import sys
import os
from datetime import datetime

# Add /app to path to import core_lib
sys.path.insert(0, '/app')

try:
    from core_lib.db import get_db_connection
except ImportError:
    print("❌ Error: Could not import core_lib. Make sure you run this inside the worker or api container.")
    print("Usage: docker compose exec -T worker python /app/scripts/update_macro_manual.py")
    sys.exit(1)

def get_input(prompt, current_value=None):
    """Get input from user with optional default."""
    if current_value is not None:
        user_input = input(f"{prompt} [{current_value}]: ")
        if user_input.strip() == "":
            return current_value
    else:
        user_input = input(f"{prompt}: ")
    
    if user_input.strip() == "":
        return None
    
    try:
        return float(user_input)
    except ValueError:
        print("❌ Invalid number. Please try again.")
        return get_input(prompt, current_value)

def main():
    print("=" * 60)
    print("Manual Macro Data Update")
    print("=" * 60)
    print("This script updates monthly/quarterly macro indicators.")
    print("Press Enter to keep current value.\n")

    conn = get_db_connection()
    
    # List of metrics to manage manually
    metrics = [
        'CPI_YOY', 
        'GDP_YOY', 
        'POLICY_RATE', 
        'CREDIT_GROWTH', 
        'INFLATION_RATE', 
        'GDP_GROWTH', 
        'INTEREST_RATE'
    ]
    
    updates = {}
    
    with conn.cursor() as cur:
        for metric in metrics:
            # Get current value
            cur.execute("SELECT value, last_updated FROM macro_clean WHERE metric_name = %s", (metric,))
            row = cur.fetchone()
            current_val = row[0] if row else None
            last_updated = row[1] if row else "Never"
            
            print(f"--- {metric} ---")
            print(f"Last Updated: {last_updated}")
            
            new_val = get_input(f"Enter new value for {metric}", current_val)
            
            if new_val is not None and new_val != current_val:
                updates[metric] = new_val
                print(f"✅ Staged for update: {new_val}")
            else:
                print("⏭️  Skipping (unchanged)")
            print("")

    if not updates:
        print("No changes made.")
        conn.close()
        return

    print("=" * 60)
    print("Summary of Changes:")
    for k, v in updates.items():
        print(f"  {k}: {v}")
    
    confirm = input("\nCommit these changes? (y/N): ")
    if confirm.lower() != 'y':
        print("❌ Cancelled.")
        conn.close()
        return

    # Execute updates
    try:
        with conn.cursor() as cur:
            for metric, value in updates.items():
                cur.execute("""
                    INSERT INTO macro_clean (metric_name, value, last_updated, effective_date)
                    VALUES (%s, %s, NOW(), CURRENT_DATE)
                    ON CONFLICT (metric_name)
                    DO UPDATE SET 
                        value = EXCLUDED.value,
                        last_updated = NOW(),
                        effective_date = CURRENT_DATE
                """, (metric, value))
            conn.commit()
        print("\n✅ Database updated successfully!")
    except Exception as e:
        print(f"\n❌ Error updating database: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
