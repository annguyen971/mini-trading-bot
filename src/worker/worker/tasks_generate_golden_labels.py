"""
Golden Label Generation - Sniper Strategy
==========================================
Generates high-quality training labels based on actual T+5 forward returns.

Logic (Sniper):
- Label 1 (BUY): T+5 return over 1.5 pct (Clear winner)
- Label 0 (NO_BUY): T+5 return under 0.5 pct (Include marginal gains, break-even, losses)
- Skip: 0.5-1.5 pct range (Gray zone - unclear outcome)

This ensures the model learns to identify only HIGH-QUALITY setups.
"""

import os
from contextlib import contextmanager
import psycopg
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@contextmanager
def pg_conn():
    """Database connection context manager."""
    dsn = os.getenv("DB_URL") or os.getenv("PG_DSN")
    if not dsn:
        raise ValueError("Neither DB_URL nor PG_DSN environment variable is set")
    with psycopg.connect(dsn) as conn:
        conn.autocommit = False
        yield conn

def generate_golden_labels_from_returns(lookback_days=365):
    """
    Generates golden labels based on actual T+5 returns.
    
    Args:
        lookback_days: Number of days to look back for label generation
    
    Returns:
        Number of labels generated
    """
    logger.info(f"=== Golden Label Generation (Sniper Logic) ===")
    logger.info(f"Lookback: {lookback_days} days")
    logger.info(f"Target: T+5 forward returns")
    
    with pg_conn() as conn:
        with conn.cursor() as cur:
            # Calculate start date in Python (safe - not user input)
            from datetime import datetime, timedelta
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
            
            # Build SQL with f-string (safe since start_date is from Python datetime)
            # Clear old labels first to avoid duplicates/stale data
            cur.execute("DELETE FROM labels_golden WHERE actor = 'sniper_t5_return'")
            
            # Build SQL with CTE and LEAD() for correct T+5 trading days
            sql = f"""
                WITH returns AS (
                    SELECT 
                        symbol,
                        trade_date,
                        close,
                        LEAD(close, 5) OVER (PARTITION BY symbol ORDER BY trade_date) as close_t5
                    FROM ta_silver
                    WHERE trade_date >= '{start_date}'
                )
                INSERT INTO labels_golden (symbol, effective_date, new_label, actor, created_at)
                SELECT 
                    symbol,
                    trade_date as effective_date,
                    CASE
                        WHEN (close_t5 - close) / NULLIF(close, 0) > 0.015 
                            THEN 1  -- BUY: gain over 1.5 pct
                        WHEN (close_t5 - close) / NULLIF(close, 0) < 0.005 
                            THEN 0  -- NO_BUY: gain under 0.5 pct
                        ELSE NULL
                    END as new_label,
                    'sniper_t5_return' as actor,
                    NOW()
                FROM returns
                WHERE close_t5 IS NOT NULL
                  AND close > 0
                  AND (
                    (close_t5 - close) / NULLIF(close, 0) > 0.015 
                    OR (close_t5 - close) / NULLIF(close, 0) < 0.005
                  );
            """
            
            cur.execute(sql)
            row_count = cur.rowcount
            
            logger.info(f"✓ Generated {row_count} golden labels")
            
            # Statistics
            cur.execute("""
                SELECT 
                    new_label,
                    COUNT(*) as count
                FROM labels_golden
                WHERE actor = 'sniper_t5_return'
                GROUP BY new_label
                ORDER BY new_label
            """)
            
            logger.info("\nLabel Distribution:")
            total = 0
            results = cur.fetchall()
            for row in results:
                total += row[1]
            
            for row in results:
                label = "BUY" if row[0] == 1 else "NO_BUY"
                pct = (row[1] / total * 100) if total > 0 else 0
                logger.info(f"  {label}: {row[1]} ({pct:.1f}%)")
        
        conn.commit()
        logger.info(f"\n✓ Golden labels committed to database")
        
        return row_count

def main():
    """Main entry point."""
    try:
        lookback = int(os.getenv("LOOKBACK_DAYS", "365"))
        count = generate_golden_labels_from_returns(lookback)
        
        logger.info("=" * 60)
        logger.info(f"SUCCESS: Generated {count} golden labels")
        logger.info("Next: Re-train model with FORCE_TRAIN=true")
        logger.info("=" * 60)
        
        return 0
    except Exception as e:
        logger.exception(f"ERROR: {e}")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
