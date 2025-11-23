import os
import sys
import logging
import argparse
import time
from datetime import datetime, timedelta
import psycopg2
from typing import List

# Add src/scraper to path to import from scraper package
sys.path.append(os.path.join(os.path.dirname(__file__), '../'))
# Add src/core_lib to path to import core_lib package
sys.path.append(os.path.join(os.path.dirname(__file__), '../../core_lib'))
# Add src to path just in case
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))

from scraper.vnstock_source import VnStockSource
from scraper.adapters import adapt_ta_data
from scraper.ingestion import process_and_ingest_data
from scraper.google_news_source import GoogleNewsSource
from scripts.backfill_news import NewsBackfiller

# Worker import is conditional - only imported if needed
# from worker.tasks_feature_gold import run_feature_gold_batch

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_db_connection():
    return psycopg2.connect(os.environ['DB_URL'])

def get_active_watchlist() -> List[str]:
    """Fetches active symbols from database."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT symbol FROM symbol_watchlist WHERE is_active = true ORDER BY symbol")
            symbols = [row[0] for row in cur.fetchall()]
        conn.close()
        return symbols
    except Exception as e:
        logger.error(f"Failed to fetch watchlist: {e}")
        return []

def backfill_ta(symbol: str, months: int, dry_run: bool = False):
    """Backfills TA data (OHLCV) for a symbol."""
    logger.info(f"--- TA Backfill: {symbol} ---")
    if dry_run:
        logger.info(f"[Dry Run] Would fetch {months} months of OHLCV for {symbol}")
        return

    try:
        source = VnStockSource()
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=months * 30)).strftime('%Y-%m-%d')
        
        logger.info(f"Fetching OHLCV from {start_date} to {end_date}")
        data = source.get_ohlcv(symbol, start_date, end_date)
        
        if not data:
            logger.warning(f"No price data found for {symbol}")
            return

        count = 0
        for record in data:
            adapted = adapt_ta_data(record)
            process_and_ingest_data(
                raw_data=adapted.model_dump(),
                source_name="vnstock",
                task_kind="TA_PROCESS"
            )
            count += 1
        
        logger.info(f"Successfully processed {count} price records for {symbol}")
        
    except Exception as e:
        logger.error(f"Failed to backfill TA for {symbol}: {e}")

def backfill_sa(symbol: str, months: int, source_name: str, delay: int, dry_run: bool = False):
    """Backfills SA data (News) for a symbol."""
    logger.info(f"--- SA Backfill ({source_name}): {symbol} ---")
    if dry_run:
        logger.info(f"[Dry Run] Would fetch {months} months of news from {source_name} for {symbol}")
        return

    try:
        backfiller = NewsBackfiller(source_name=source_name, delay_seconds=delay)
        backfiller.run(months_back=months, specific_symbol=symbol)
    except Exception as e:
        logger.error(f"Failed to backfill SA ({source_name}) for {symbol}: {e}")

def run_analysis(dry_run: bool = False):
    """Runs the feature engineering pipeline."""
    logger.info("--- Running Analysis Pipeline ---")
    if dry_run:
        logger.info("[Dry Run] Would run tasks_feature_gold.py")
        return

    try:
        # Import only when needed
        import sys
        sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))
        from worker.tasks_feature_gold import run_feature_gold_batch
        
        run_feature_gold_batch()
        logger.info("Analysis pipeline completed successfully")
    except Exception as e:
        logger.error(f"Analysis pipeline failed: {e}")

def main():
    parser = argparse.ArgumentParser(description="Run Full Data Pipeline")
    parser.add_argument("--months", type=int, default=6, help="Months of history to backfill")
    parser.add_argument("--delay", type=int, default=5, help="Delay between requests (seconds)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    parser.add_argument("--skip-ta", action="store_true", help="Skip TA backfill")
    parser.add_argument("--skip-sa", action="store_true", help="Skip SA backfill")
    parser.add_argument("--skip-analysis", action="store_true", help="Skip Analysis")
    
    args = parser.parse_args()
    
    logger.info("Starting Full Pipeline Execution")
    logger.info(f"Months: {args.months}, Delay: {args.delay}s, Dry Run: {args.dry_run}")

    # 1. Get Watchlist
    symbols = get_active_watchlist()
    if not symbols:
        logger.error("No active symbols found in watchlist.")
        return
    
    logger.info(f"Found {len(symbols)} active symbols: {', '.join(symbols)}")

    # 2. Backfill Data per Symbol
    for i, symbol in enumerate(symbols):
        logger.info(f"\n[{i+1}/{len(symbols)}] Processing {symbol}...")
        
        # TA Backfill
        if not args.skip_ta:
            backfill_ta(symbol, args.months, args.dry_run)
            time.sleep(1) # Brief pause
            
        # SA Backfill (Google)
        if not args.skip_sa:
            backfill_sa(symbol, args.months, "google", args.delay, args.dry_run)
            
        # SA Backfill (VnStock) - Optional, can be enabled if needed
        # backfill_sa(symbol, args.months, "vnstock", args.delay, args.dry_run)

    # 3. Run Analysis (Once for all symbols)
    if not args.skip_analysis:
        run_analysis(args.dry_run)

    logger.info("\nFull Pipeline Execution Completed!")

if __name__ == "__main__":
    main()
