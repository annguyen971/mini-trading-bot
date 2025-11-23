#!/usr/bin/env python3
"""
News Backfill Script - Historical Data Collection
==================================================
Implements PRD Story 1.5 AC1.5.3: Backfill 12-24 months historical news

Features:
- Safe low-rate backfill (configurable delay)
- Progress tracking and resume capability
- Date range validation
- Error handling and retry logic
- Supports multiple sources: vnstock, google

Usage:
    # Backfill last 12 months for all symbols using vnstock
    python backfill_news.py --months 12

    # Backfill using Google Search for FPT
    python backfill_news.py --source google --symbol FPT --months 6

    # Resume interrupted backfill
    python backfill_news.py --months 12 --resume

Example:
    $ python backfill_news.py --source google --months 6
    Starting backfill for 10 symbols, 6 months back
    Progress: FPT [====================] 100% (180 days)
    ...
"""

import argparse
import sys
import time
import uuid
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict, Any
import logging

# Add parent directory to path for imports
sys.path.insert(0, '/root/mini-trading-bot/mini-trading-bot/src/scraper')
sys.path.insert(0, '/root/mini-trading-bot/mini-trading-bot/src/core_lib')

from scraper.vnstock_source import VnStockSource
from scraper.google_news_source import GoogleNewsSource
from scraper.adapters import adapt_sa_data
from scraper.ingestion import process_and_ingest_data
from scraper.state_manager import ScraperStateManager, create_source_id
from core_lib.db import get_db_connection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NewsBackfiller:
    """Manages historical news backfill operations."""

    def __init__(
        self,
        source_name: str = "vnstock",
        delay_seconds: int = 2,
        max_retries: int = 3
    ):
        """
        Initialize backfiller.

        Args:
            source_name: Data source name ('vnstock' or 'google')
            delay_seconds: Delay between requests (rate limiting)
            max_retries: Maximum retries per request
        """
        self.source_name = source_name
        self.delay_seconds = delay_seconds
        self.max_retries = max_retries
        
        if source_name == "google":
            self.source = GoogleNewsSource()
        else:
            self.source = VnStockSource()
            
        self.stats = {
            'symbols_processed': 0,
            'articles_collected': 0,
            'articles_duplicates': 0,
            'errors': 0
        }

    def get_symbols(self, specific_symbol: Optional[str] = None) -> List[str]:
        """
        Get symbols to backfill.

        Args:
            specific_symbol: If provided, only backfill this symbol

        Returns:
            List of symbols
        """
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                if specific_symbol:
                    cur.execute("""
                        SELECT symbol FROM symbol_watchlist
                        WHERE user_id = 'admin'
                          AND is_active = true
                          AND symbol = %s
                    """, (specific_symbol.upper(),))
                else:
                    cur.execute("""
                        SELECT symbol FROM symbol_watchlist
                        WHERE user_id = 'admin'
                          AND is_active = true
                        ORDER BY symbol
                    """)

                symbols = [row[0] for row in cur.fetchall()]
                return symbols

        except Exception as e:
            logger.error(f"Failed to get symbols: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def backfill_symbol(
        self,
        symbol: str,
        months_back: int,
        resume: bool = False
    ) -> int:
        """
        Backfill news for a single symbol.

        Args:
            symbol: Stock symbol
            months_back: How many months to backfill
            resume: If True, check state and skip already backfilled

        Returns:
            Number of articles collected
        """
        logger.info(f"Backfilling {symbol} - {months_back} months (Source: {self.source_name})")

        conn = None
        articles_count = 0

        try:
            conn = get_db_connection()
            state_manager = ScraperStateManager(conn)
            source_id = create_source_id(self.source_name, symbol, 'news')

            # Check if already backfilled (resume mode)
            if resume:
                cursor = state_manager.get_cursor(source_id)
                if cursor and cursor.startswith('BACKFILL_COMPLETE'):
                    logger.info(f"  ✓ {symbol} already backfilled (cursor: {cursor})")
                    return 0

            # Calculate date range
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=months_back * 30)

            logger.info(f"  Date range: {start_date} to {end_date}")

            if self.source_name == "google":
                articles_count = self._backfill_google(symbol, start_date, end_date)
            else:
                articles_count = self._backfill_vnstock(symbol, months_back)

            # Mark as backfilled
            backfill_cursor = f"BACKFILL_COMPLETE_{end_date.strftime('%Y%m%d')}"
            state_manager.update_cursor(source_id, backfill_cursor)

            logger.info(f"  ✓ {symbol}: {articles_count} articles collected")
            self.stats['articles_collected'] += articles_count
            self.stats['symbols_processed'] += 1

        except Exception as e:
            logger.error(f"  ✗ Error backfilling {symbol}: {e}")
            self.stats['errors'] += 1

        finally:
            if conn:
                conn.close()

        return articles_count

    def _backfill_vnstock(self, symbol: str, months_back: int) -> int:
        """Backfill using vnstock source (best effort)."""
        retry_count = 0
        articles = None

        while retry_count < self.max_retries:
            try:
                days_back = months_back * 30
                articles = self.source.fetch_latest_news(symbol, days=days_back)
                break
            except Exception as e:
                retry_count += 1
                logger.warning(f"  Retry {retry_count}/{self.max_retries} for {symbol}: {e}")
                time.sleep(self.delay_seconds * retry_count)

        if articles is None:
            logger.error(f"  ✗ Failed to fetch news for {symbol} after {self.max_retries} retries")
            self.stats['errors'] += 1
            return 0

        count = 0
        for article in articles:
            try:
                article_payload = {
                    "id": article.get("id"),
                    "source": article.get("source"),
                    "url": article.get("url"),
                    "text": article.get("text"),
                    "publisher_time": article.get("published_at"),
                    "first_seen_time": article.get("first_seen_time"),
                    "entities": [symbol]
                }
                self._ingest_article(article_payload)
                count += 1
            except Exception as e:
                logger.debug(f"  Skipped article: {e}")
                self.stats['articles_duplicates'] += 1
            
            time.sleep(self.delay_seconds)
            
        return count

    def _backfill_google(self, symbol: str, start_date: date, end_date: date) -> int:
        """Backfill using Google Search with sliding window."""
        chunk_size_days = 7
        current_chunk_end = end_date
        total_count = 0

        while current_chunk_end > start_date:
            current_chunk_start = max(start_date, current_chunk_end - timedelta(days=chunk_size_days))
            
            logger.info(f"    Fetching chunk: {current_chunk_start} -> {current_chunk_end}")
            
            retry_count = 0
            articles = []
            
            while retry_count < self.max_retries:
                try:
                    articles = self.source.fetch_news_chunk(symbol, current_chunk_start, current_chunk_end)
                    break
                except Exception as e:
                    retry_count += 1
                    logger.warning(f"    Retry {retry_count}/{self.max_retries}: {e}")
                    # Exponential backoff
                    time.sleep(self.delay_seconds * (2 ** retry_count))

            if not articles:
                logger.debug(f"    No articles found in chunk.")
            else:
                for item in articles:
                    try:
                        # Map Google result to SAData
                        # Google returns: {'title': ..., 'url': ..., 'source': ..., 'published_at': ...}
                        
                        # Generate a deterministic ID based on URL
                        article_id = str(uuid.uuid5(uuid.NAMESPACE_URL, item.get('url', '')))
                        
                        # Parse published_at if possible, else use current time or chunk start
                        pub_time = item.get('published_at')
                        if not pub_time:
                            pub_time = datetime.combine(current_chunk_start, datetime.min.time())
                        elif isinstance(pub_time, str):
                            try:
                                # Try parsing common formats if needed, but Gemini might return ISO
                                pub_time = datetime.fromisoformat(pub_time.replace('Z', '+00:00'))
                            except:
                                pub_time = datetime.combine(current_chunk_start, datetime.min.time())

                        article_payload = {
                            "id": article_id,
                            "source": item.get("source", "google_search"),
                            "url": item.get("url"),
                            "title": item.get("title"),
                            "text": f"{item.get('title')} {item.get('snippet', '')}".strip(),
                            "publisher_time": pub_time,
                            "first_seen_time": datetime.now(),
                            "entities": [symbol]
                        }
                        
                        self._ingest_article(article_payload)
                        total_count += 1
                    except Exception as e:
                        logger.debug(f"    Skipped article: {e}")
                        self.stats['articles_duplicates'] += 1

            # Move window back
            current_chunk_end = current_chunk_start - timedelta(days=1)
            
            # Rate limiting between chunks
            time.sleep(self.delay_seconds)

        return total_count

    def _ingest_article(self, article_payload: Dict[str, Any]):
        """Helper to adapt and ingest article data."""
        adapted_data = adapt_sa_data(article_payload)
        process_and_ingest_data(
            raw_data=adapted_data.model_dump(),
            source_name=self.source_name,
            task_kind="NLP_PROCESS"
        )

    def run(
        self,
        months_back: int,
        specific_symbol: Optional[str] = None,
        resume: bool = False
    ):
        """
        Run backfill for all symbols or specific symbol.

        Args:
            months_back: How many months to backfill
            specific_symbol: Optional - only backfill this symbol
            resume: Skip already backfilled symbols
        """
        symbols = self.get_symbols(specific_symbol)

        if not symbols:
            logger.error("No symbols found to backfill")
            return

        logger.info("=" * 60)
        logger.info(f"Starting backfill: {len(symbols)} symbols, {months_back} months")
        logger.info(f"Source: {self.source_name}")
        logger.info(f"Rate limit: {self.delay_seconds}s delay between requests")
        logger.info(f"Resume mode: {'ENABLED' if resume else 'DISABLED'}")
        logger.info("=" * 60)

        start_time = time.time()

        for i, symbol in enumerate(symbols, 1):
            logger.info(f"\n[{i}/{len(symbols)}] Processing {symbol}")
            self.backfill_symbol(symbol, months_back, resume)

        elapsed = time.time() - start_time

        logger.info("\n" + "=" * 60)
        logger.info("Backfill Summary:")
        logger.info(f"  Symbols processed: {self.stats['symbols_processed']}")
        logger.info(f"  Articles collected: {self.stats['articles_collected']}")
        logger.info(f"  Duplicates skipped: {self.stats['articles_duplicates']}")
        logger.info(f"  Errors: {self.stats['errors']}")
        logger.info(f"  Time elapsed: {elapsed:.1f}s ({elapsed/60:.1f} minutes)")
        logger.info("=" * 60)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Backfill historical news data for Stock Hunter AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backfill 12 months for all symbols
  python backfill_news.py --months 12

  # Backfill using Google Search for FPT
  python backfill_news.py --source google --symbol FPT --months 6

  # Resume interrupted backfill
  python backfill_news.py --months 12 --resume
        """
    )

    parser.add_argument(
        '--months',
        type=int,
        default=12,
        help='Number of months to backfill (default: 12, max: 24)'
    )

    parser.add_argument(
        '--symbol',
        type=str,
        default=None,
        help='Specific symbol to backfill (default: all active symbols)'
    )
    
    parser.add_argument(
        '--source',
        type=str,
        default='vnstock',
        choices=['vnstock', 'google'],
        help='Data source to use (default: vnstock)'
    )

    parser.add_argument(
        '--delay',
        type=int,
        default=2,
        help='Delay between requests in seconds (default: 2, min: 1)'
    )

    parser.add_argument(
        '--resume',
        action='store_true',
        help='Skip symbols that have already been backfilled'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be backfilled without actually doing it'
    )

    args = parser.parse_args()

    # Validation
    if args.months < 1 or args.months > 24:
        logger.error("Months must be between 1 and 24")
        sys.exit(1)

    if args.delay < 1:
        logger.error("Delay must be at least 1 second")
        sys.exit(1)

    # Dry run
    if args.dry_run:
        logger.info("DRY RUN MODE - No data will be collected")
        backfiller = NewsBackfiller(source_name=args.source, delay_seconds=args.delay)
        symbols = backfiller.get_symbols(args.symbol)
        logger.info(f"Would backfill {len(symbols)} symbols: {', '.join(symbols)}")
        logger.info(f"Source: {args.source}")
        logger.info(f"Date range: ~{args.months} months back")
        logger.info(f"Rate limit: {args.delay}s delay")
        sys.exit(0)

    # Run backfill
    backfiller = NewsBackfiller(source_name=args.source, delay_seconds=args.delay)
    backfiller.run(
        months_back=args.months,
        specific_symbol=args.symbol,
        resume=args.resume
    )


if __name__ == "__main__":
    main()
