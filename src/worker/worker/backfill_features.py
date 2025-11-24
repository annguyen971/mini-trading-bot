import os
import sys
from datetime import datetime, timedelta
from worker.tasks_feature_gold import run_feature_gold_batch

def main():
    # Backfill for the last 6 months
    start_date = datetime(2025, 5, 21)
    end_date = datetime(2025, 11, 21)
    delta = end_date - start_date

    print(f"Backfilling features from {start_date.date()} to {end_date.date()}")

    for i in range(delta.days + 1):
        d = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
        print(f"Processing features for {d}...")
        os.environ["AS_OF_DATE"] = d
        try:
            run_feature_gold_batch()
        except Exception as e:
            print(f"Error processing {d}: {e}")

if __name__ == "__main__":
    main()
