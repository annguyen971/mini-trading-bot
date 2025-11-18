from datetime import datetime, timedelta
from scraper.ingestion import process_and_ingest_data
from scraper.utils import RateLimiter, CircuitBreaker
from core_lib.db import get_db_connection
from scraper.vnstock_source import VnStockSource
from scraper.adapters import adapt_ta_data, adapt_sa_data

# --- Defensive Mechanisms Setup ---
rate_limiter = RateLimiter(requests_per_minute=60)
circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=600)

# --- Configuration ---
WATCHLIST = ["FPT", "TCB", "VNM"]
NEWS_LOOKBACK_DAYS = 7
PRICE_LOOKBACK_DAYS = 90

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
    print("Scraper worker starting...")
    source_name = "vnstock"
    source = VnStockSource()

    # --- FIX: Functional Backpressure Implementation ---
    if check_backpressure():
        print("Backpressure ENABLED. Reducing rate to 30 req/min.")
        rate_limiter.set_requests_per_minute(30)

    # --- Fetch, Adapt, and Ingest Data ---
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

            # --- Fetch News Data (SA) ---
            rate_limiter.wait(f"{source_name}:{symbol}:news")
            print(f"Fetching news for {symbol}...")
            raw_news_data = source.fetch_latest_news(symbol, days=NEWS_LOOKBACK_DAYS)

            if raw_news_data:
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
                    adapted_data = adapt_sa_data(article_payload)
                    process_and_ingest_data(
                        raw_data=adapted_data.model_dump(),
                        source_name=source_name,
                        task_kind="NLP_PROCESS"
                    )
                print(f"Successfully processed {len(raw_news_data)} news articles for {symbol}.")

            circuit_breaker.record_success(f"{source_name}:{symbol}")

        except Exception as e:
            print(f"An error occurred while processing {symbol}: {e}")
            circuit_breaker.record_failure(f"{source_name}:{symbol}")

    print("\nScraper worker finished.")

if __name__ == "__main__":
    main()
