import os
import sys
from datetime import datetime, timedelta
from worker.tasks_label import run_label_batch

def main():
    # Backfill for the last 6 months (same as TA data)
    start_date = datetime(2025, 5, 21)
    end_date = datetime(2025, 11, 21)
    delta = end_date - start_date

    print(f"Backfilling labels from {start_date.date()} to {end_date.date()}")

    for i in range(delta.days + 1):
        d = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
        print(f"Processing {d}...")
        try:
            run_label_batch(target_date=d)
        except Exception as e:
            print(f"Error processing {d}: {e}")

if __name__ == "__main__":
    main()
