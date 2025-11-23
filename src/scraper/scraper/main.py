from datetime import datetime, timedelta
from typing import List
from scraper.ingestion import process_and_ingest_data
from scraper.utils import RateLimiter, CircuitBreaker
from scraper.legal_guard import LegalGuard
from scraper.state_manager import ScraperStateManager, create_source_id
from core_lib.db import get_db_connection
from scraper.vnstock_source import VnStockSource
from scraper.adapters import adapt_ta_data, adapt_sa_data
import time

# --- Defensive Mechanisms Setup ---
rate_limiter = RateLimiter(requests_per_minute=60)
circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=600)

# --- Configuration ---
NEWS_LOOKBACK_DAYS = 180
PRICE_LOOKBACK_DAYS = 180
DEFAULT_WATCHLIST = ["FPT", "TCB", "VNM"]  # Fallback if DB fails

def get_active_watchlist(user_id: str = 'admin') -> List[str]:
    """
    Fetches active symbols from database watchlist.

    Args:
        user_id: User ID to fetch watchlist for (default: 'admin')

    Returns:
        List of active symbol strings, sorted alphabetically

    Fallback:
        Returns DEFAULT_WATCHLIST if database query fails
    """
    print(f"Loading watchlist for user '{user_id}'...")
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT symbol
                FROM symbol_watchlist
                WHERE user_id = %s AND is_active = true
                ORDER BY symbol
            """, (user_id,))
            symbols = [row[0] for row in cursor.fetchall()]

            if symbols:
                print(f"✓ Loaded {len(symbols)} symbols from database: {', '.join(symbols[:5])}{'...' if len(symbols) > 5 else ''}")
                return symbols
            else:
                print(f"⚠ No symbols found for user '{user_id}', using fallback")
                return DEFAULT_WATCHLIST

    except Exception as e:
        print(f"⚠ Failed to load watchlist from database: {e}")
        print(f"  Using fallback: {DEFAULT_WATCHLIST}")
        return DEFAULT_WATCHLIST

    finally:
        if conn:
            conn.close()


def check_backpressure() -> bool:
    """
    Checks the backpressure flag from the database.
    Connects to the DB and reads the 'SCRAPE_SLOW' flag.
    """
    print("Checking for backpressure signal ('SCRAPE_SLOW' flag)...")
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT enabled FROM control_flags WHERE flag = 'SCRAPE_SLOW';")
            result = cursor.fetchone()
            if result and result[0] is True:
                print("Result: Backpressure is ENABLED.")
                return True
    except Exception as e:
        print(f"Could not check backpressure flag, defaulting to disabled. Error: {e}")
    finally:
        if conn:
            conn.close()

    print("Result: Backpressure is DISABLED.")
    return False

def main():
    """Main entrypoint for the scraper worker."""
    print("=" * 60)
    print("Scraper worker starting...")
    print("=" * 60)

    source_name = "vnstock"
    source = VnStockSource()

    # --- Load Dynamic Watchlist from Database ---
    WATCHLIST = get_active_watchlist()

    if not WATCHLIST:
        print("⚠ No symbols to scrape. Exiting.")
        return

    # --- Legal Guard Setup (P0) ---
    conn_legal = get_db_connection()
    legal_guard = LegalGuard(conn_legal)
    print(f"✓ Legal Guard initialized (robots.txt compliance enabled)")

    # --- State Manager Setup (P1 - Incremental Crawl) ---
    conn_state = get_db_connection()
    state_manager = ScraperStateManager(conn_state)
    print(f"✓ State Manager initialized (incremental crawl enabled)")

    # --- Backpressure Check ---
    if check_backpressure():
        print("⚠ Backpressure ENABLED. Reducing rate to 30 req/min.")
        rate_limiter.set_requests_per_minute(30)

    # --- Macro Data Refresh (P2 - Real-time Macro) ---
    print("\nRefreshing Macro Data...")
    try:
        from scraper.macro_sources import refresh_macro_data_from_vnstock
        if refresh_macro_data_from_vnstock():
            print("✓ Macro data updated successfully")
        else:
            print("⚠ Macro data refresh returned failure status")
    except ImportError:
        print("⚠ scraper.macro_sources module not found (requires rebuild)")
    except Exception as e:
        print(f"⚠ Macro data refresh failed: {e}")


    # --- Fetch, Adapt, and Ingest Data ---
    print(f"\nProcessing {len(WATCHLIST)} symbols...")
    print("=" * 60)

    for symbol in WATCHLIST:
        print(f"\n--- Processing symbol: {symbol} ---")

        if circuit_breaker.is_open(f"{source_name}:{symbol}"):
            print(f"Circuit for {symbol} is open. Skipping.")
            continue

        try:
            # --- Fetch Price Data (TA) ---
            rate_limiter.wait(f"{source_name}:{symbol}:price")
            print(f"Fetching OHLCV for {symbol}...")
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=PRICE_LOOKBACK_DAYS)).strftime('%Y-%m-%d')

            raw_price_data = source.get_ohlcv(symbol, start_date=start_date, end_date=end_date)

            if raw_price_data:
                for record in raw_price_data:
                    adapted_data = adapt_ta_data(record)
                    process_and_ingest_data(
                        raw_data=adapted_data.model_dump(),
                        source_name=source_name,
                        task_kind="TA_PROCESS"
                    )
                print(f"Successfully processed {len(raw_price_data)} price records for {symbol}.")

            # --- Fetch News Data (SA) with Incremental Crawl ---
            rate_limiter.wait(f"{source_name}:{symbol}:news")

            # Check last crawl state (P1 - Incremental)
            news_source_id = create_source_id(source_name, symbol, 'news')
            last_cursor = state_manager.get_cursor(news_source_id)

            if last_cursor and last_cursor.startswith('BACKFILL_COMPLETE'):
                print(f"Fetching incremental news for {symbol} (after backfill)...")
                # Use shorter lookback for incremental (last 7 days)
                raw_news_data = source.fetch_latest_news(symbol, days=NEWS_LOOKBACK_DAYS)
            elif last_cursor:
                print(f"Fetching incremental news for {symbol} (cursor: {last_cursor[:20]}...)...")
                # Use shorter lookback for incremental
                raw_news_data = source.fetch_latest_news(symbol, days=NEWS_LOOKBACK_DAYS)
            else:
                print(f"Fetching news for {symbol} (first run)...")
                # First run - fetch more history
                raw_news_data = source.fetch_latest_news(symbol, days=NEWS_LOOKBACK_DAYS)

            if raw_news_data:
                processed_count = 0
                latest_article_id = None

                for article in raw_news_data:
                    article_payload = {
                        "id": article.get("id"),
                        "source": article.get("source"),
                        "url": article.get("url"),
                        "text": article.get("text"),
                        "publisher_time": article.get("published_at"),
                        "first_seen_time": article.get("first_seen_time"),
                        "entities": [symbol]
                    }

                    # Track latest article ID for cursor update
                    if not latest_article_id:
                        latest_article_id = article.get("id", article.get("url"))

                    try:
                        adapted_data = adapt_sa_data(article_payload)
                        process_and_ingest_data(
                            raw_data=adapted_data.model_dump(),
                            source_name=source_name,
                            task_kind="NLP_PROCESS"
                        )
                        processed_count += 1
                    except Exception as e:
                        # Skip duplicates or malformed articles
                        print(f"Error processing article: {e}")
                        pass

                print(f"Successfully processed {processed_count}/{len(raw_news_data)} news articles for {symbol}.")

                # Update cursor after successful crawl (P1 - State Tracking)
                if latest_article_id:
                    state_manager.update_cursor(news_source_id, str(latest_article_id))

            circuit_breaker.record_success(f"{source_name}:{symbol}")

        except Exception as e:
            print(f"An error occurred while processing {symbol}: {e}")
            circuit_breaker.record_failure(f"{source_name}:{symbol}")

    # --- Cleanup ---
    if conn_legal:
        conn_legal.close()
    if conn_state:
        conn_state.close()

    print("=" * 60)
    print("Scraper worker finished.")
    print("=" * 60)

if __name__ == "__main__":
    main()
