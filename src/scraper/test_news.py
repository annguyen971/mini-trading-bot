from scraper.vnstock_source import VnStockSource
import json

def test_news():
    source = VnStockSource()
    symbol = "FPT"
    print(f"Fetching news for {symbol}...")
    news = source.fetch_latest_news(symbol, days=30)
    print(f"Found {len(news)} articles.")
    if news:
        print("Sample article:")
        print(json.dumps(news[0], indent=2, default=str))

if __name__ == "__main__":
    test_news()
