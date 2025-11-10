from datetime import datetime
from ingestion import process_and_ingest_data
from utils import RateLimiter, CircuitBreaker
from core_lib.db import get_db_connection

# --- Defensive Mechanisms Setup ---
# AC2: Rate Limiter (60 req/min) and Circuit Breaker (5 failures -> 10 min timeout)
rate_limiter = RateLimiter(requests_per_minute=60)
circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=600)

def check_backpressure() -> bool:
    """
    (AC3) Checks the backpressure flag from the database.
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

def fetch_data_from_source(source_domain: str):
    """
    Simulates fetching data, respecting defensive mechanisms.
    """
    if circuit_breaker.is_open(source_domain):
        print(f"Circuit for {source_domain} is open. Skipping request.")
        return None

    rate_limiter.wait(source_domain)

    try:
        print(f"Fetching data from {source_domain}...")
        # In a real app, this would be an HTTP request.
        # We'll simulate a failure for demonstration.
        if "fail" in source_domain:
             raise ValueError("Simulated network failure")

        circuit_breaker.record_success(source_domain)
        return {
            "id": "sa-12345", "source": source_domain, "author": "John Doe",
            "url": f"http://{source_domain}/article/123", "text": "This is a sample news article about a stock.",
            "publisher_time": datetime(2025, 11, 9, 15, 30, 0), "entities": ["STOCK_ABC"]
        }
    except Exception as e:
        print(f"Failed to fetch data from {source_domain}: {e}")
        circuit_breaker.record_failure(source_domain)
        return None

def main():
    """Main entrypoint for the scraper worker."""
    print("Scraper worker starting...")

    is_slowdown_enabled = check_backpressure()

    if is_slowdown_enabled:
        print("Backpressure enabled: scraper will run at 50% speed.")
    else:
        print("Backpressure disabled: scraper will run at full speed.")

    # --- Simulate fetching and processing data events ---
    source_domains = ["example-news.com", "failing-source.com"]
    for domain in source_domains * 4: # Multiply to test circuit breaker
        print(f"\n--- Simulating event from {domain} ---")
        raw_data = fetch_data_from_source(domain)

        if raw_data:
            process_and_ingest_data(
                raw_data=raw_data,
                source_name=domain,
                task_kind="process_sa_data"
            )

    print("\nScraper worker finished.")

if __name__ == "__main__":
    main()
