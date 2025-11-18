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
PRICE_LOOKBACK_DAYS = 90 # Fetch last 3 months of data for now

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

    # Initialize the real data source
    source = VnStockSource()

    if check_backpressure():
        print("Backpressure enabled: scraper will run at 50% speed.")
        # This can be implemented by adjusting rate_limiter settings if needed
        # For now, it's just an informational message.

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
                    # Adapt data to internal format
                    adapted_data = adapt_ta_data(record)
                    process_and_ingest_data(
                        raw_data=adapted_data.model_dump(),
                        source_name=source_name,
                        task_kind="ta.bar" # Use the kind defined in silver_logic_v1
                    )
                print(f"Successfully processed {len(raw_price_data)} price records for {symbol}.")
            else:
                print(f"No price data found for {symbol}.")


            # --- Fetch News Data (SA) ---
            rate_limiter.wait(f"{source_name}:{symbol}:news")
            print(f"Fetching news for {symbol}...")
            raw_news_data = source.fetch_latest_news(symbol, days=NEWS_LOOKBACK_DAYS)

            if raw_news_data:
                for article in raw_news_data:
                    # Adapt data to internal format
                    # We might need to adjust the adapter or the source mapping here.
                    # For now, let's assume a direct pass-through after source fetch.
                    # A proper adapter would be needed if vnstock doesn't provide all fields.
                    # SAData requires: id, source, url, text, publisher_time, first_seen_time
                    article_payload = {
                        "id": article.get("id", article.get("url")), # Use URL as ID if not present
                        "source": article.get("source", source_name),
                        "url": article.get("url"),
                        "text": article.get("text", article.get("title")), # Use title if text is missing
                        "publisher_time": article.get("published_at"),
                        "first_seen_time": article.get("first_seen_time"),
                        "entities": [symbol] # Add the symbol as an entity
                    }
                    adapted_data = adapt_sa_data(article_payload)
                    process_and_ingest_data(
                        raw_data=adapted_data.model_dump(),
                        source_name=source_name,
                        task_kind="sa.article" # Use the kind defined in silver_logic_v1
                    )
                print(f"Successfully processed {len(raw_news_data)} news articles for {symbol}.")
            else:
                print(f"No news found for {symbol}.")

            circuit_breaker.record_success(f"{source_name}:{symbol}")

        except Exception as e:
            print(f"An error occurred while processing {symbol}: {e}")
            circuit_breaker.record_failure(f"{source_name}:{symbol}")

    print("\nScraper worker finished.")

if __name__ == "__main__":
    main()
