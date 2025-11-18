import pandas as pd
from datetime import datetime, timedelta
from typing import List
import logging

# Assuming vnstock3 is the correct package name
from vnstock3 import Vnstock

from core_lib.core_lib.data_source import PriceSource, NewsSource

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VnStockSource(PriceSource, NewsSource):
    """
    An implementation of PriceSource and NewsSource using the vnstock library.
    This version propagates exceptions to be handled by an upper layer (e.g., a circuit breaker).
    """
    def __init__(self):
        self.stock = Vnstock()

    def get_ohlcv(self, symbol: str, start_date: str, end_date: str) -> List[dict]:
        """
        Fetches historical OHLCV data using vnstock.
        Maps the vnstock DataFrame to the required internal format.
        Raises exceptions on failure.
        """
        try:
            logger.info(f"Fetching OHLCV for {symbol} from {start_date} to {end_date}")
            df = self.stock.stock.historical_data(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date
            )

            if df is None or df.empty:
                logger.warning(f"No OHLCV data returned for {symbol}")
                return []

            # Data Mapping: Ensure column names match TAData model in adapters.py
            df_renamed = df.rename(columns={
                'time': 'trade_date',
                'value': 'turnover' # 'value' is aliased to 'turnover' in TAData
            })

            df_renamed['symbol'] = symbol
            df_renamed['trade_date'] = pd.to_datetime(df_renamed['trade_date']).dt.date

            return df_renamed.to_dict('records')

        except Exception as e:
            logger.error(f"vnstock failed to fetch OHLCV for {symbol}: {e}")
            # Re-raise the exception to allow the circuit breaker to catch it
            raise

    def fetch_latest_news(self, symbol: str, days: int) -> List[dict]:
        """
        Fetches the latest news for a symbol using vnstock.
        Maps the vnstock DataFrame to the required internal format.
        Raises exceptions on failure.
        """
        try:
            logger.info(f"Fetching latest news for {symbol} for the last {days} days")
            # Fetch a reasonable number of recent news to filter from
            news_df = self.stock.stock.news(symbol=symbol, page_size=30, page_num=1)

            if news_df is None or news_df.empty:
                logger.warning(f"No news data returned for {symbol}")
                return []

            # Data Mapping: Ensure column names match SAData model in adapters.py
            news_df_renamed = news_df.rename(columns={
                'description': 'text' # Using description as the main text content
            })

            # Filter by date
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            news_df_renamed['published_at'] = pd.to_datetime(news_df_renamed['published_at'])
            filtered_df = news_df_renamed[news_df_renamed['published_at'] >= start_date].copy()

            # Add placeholder for first_seen_time, as vnstock doesn't provide it
            filtered_df['first_seen_time'] = datetime.now()

            return filtered_df.to_dict('records')

        except Exception as e:
            logger.error(f"vnstock failed to fetch news for {symbol}: {e}")
            # Re-raise the exception to allow the circuit breaker to catch it
            raise
