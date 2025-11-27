#!/usr/bin/env python3
"""
Backfill sector_stats for the last 14 days after fixing sector mapping.
This ensures RRG trails have sufficient history to display properly.

Usage: python backfill_sector_stats.py
"""
import os
import sys
import psycopg
from datetime import datetime, timedelta
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add worker path for imports
sys.path.insert(0, '/app/worker')

# Import the calculation function
from tasks_feature_gold import calculate_sector_stats

DB_URL = os.getenv("DB_URL", "postgresql://hunter:hunter123@db:5432/hunter")

def backfill_last_n_days(n_days=14):
    """
    Backfill sector stats for last N days.
    This recalculates RRG metrics with the corrected sector mapping.
    """
    # Use 2025-11-25 as the end date (latest data we have)
    end_date = datetime(2025, 11, 25).date()
    
    logger.info(f"Starting backfill for {n_days} days ending on {end_date}")
    
    conn = psycopg.connect(DB_URL)
    success_count = 0
    error_count = 0
    
    try:
        for i in range(n_days):
            target_date = end_date - timedelta(days=i)
            logger.info(f"[{i+1}/{n_days}] Processing {target_date}...")
            
            try:
                # Calculate sector stats for this date
                calculate_sector_stats(conn, target_date)
                success_count += 1
                logger.info(f"  ✓ Successfully processed {target_date}")
            except Exception as e:
                error_count += 1
                logger.error(f"  ✗ Error processing {target_date}: {e}")
                # Continue with next date even if this one fails
                continue
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Backfill complete!")
        logger.info(f"Success: {success_count}/{n_days} days")
        logger.info(f"Errors: {error_count}/{n_days} days")
        logger.info(f"{'='*60}\n")
        
    finally:
        conn.close()

if __name__ == "__main__":
    try:
        backfill_last_n_days(14)
    except KeyboardInterrupt:
        logger.warning("\nBackfill interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
