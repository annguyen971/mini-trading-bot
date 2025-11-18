from typing import Protocol, List
from datetime import date

class PriceSource(Protocol):
    """
    Interface for a source providing stock price data (OHLCV).
    """
    def get_ohlcv(self, symbol: str, start_date: str, end_date: str) -> List[dict]:
        """
        Fetches historical OHLCV data for a given symbol and date range.

        Args:
            symbol: The stock symbol (e.g., "FPT").
            start_date: The start date in "YYYY-MM-DD" format.
            end_date: The end date in "YYYY-MM-DD" format.

        Returns:
            A list of dictionaries, where each dictionary represents a trading day's data.
            Example: [{'time': '2023-01-01', 'open': 100, 'high': 102, 'low': 99, 'close': 101, 'volume': 1000000}]
        """
        ...

class NewsSource(Protocol):
    """
    Interface for a source providing stock-related news.
    """
    def fetch_latest_news(self, symbol: str, days: int) -> List[dict]:
        """
        Fetches the latest news articles for a given symbol over the past number of days.

        Args:
            symbol: The stock symbol (e.g., "FPT").
            days: The number of recent days to look back for news.

        Returns:
            A list of dictionaries, where each dictionary represents a news article.
            Example: [{'url': '...', 'title': '...', 'content': '...', 'published_at': '...'}]
        """
        ...
