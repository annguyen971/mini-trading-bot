#!/usr/bin/env python3
"""
Backfill sector_stats for specific dates (small batch to avoid OOM).
"""
import os
import sys
import psycopg
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

sys.path.insert(0, '/app/worker')
from tasks_feature_gold import calculate_sector_stats

DB_URL = os.getenv("DB_URL", "postgresql://hunter:hunter123@db:5432/hunter")

# Backfill these specific dates
DATES_TO_BACKFILL = [
    "2025-11-24",
    "2025-11-23",
    "2025-11-22",
]

def backfill():
    conn = psycopg.connect(DB_URL)
    
    try:
        for date_str in DATES_TO_BACKFILL:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            logger.info(f"Processing {target_date}...")
            
            try:
                calculate_sector_stats(conn, target_date)
                logger.info(f"  ✓ Success")
            except Exception as e:
                logger.error(f"  ✗ Error: {e}")
        
        logger.info(f"\n✓ Batch complete!")
    finally:
        conn.close()

if __name__ == "__main__":
    backfill()
