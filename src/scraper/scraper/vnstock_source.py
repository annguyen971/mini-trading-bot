import pandas as pd
from datetime import datetime, timedelta
from typing import List
import logging

# Updated to use 'vnstock' library
from vnstock import Vnstock

# FIX: Corrected the import path to remove the extra 'core_lib'
from core_lib.data_source import PriceSource, NewsSource

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VnStockSource(PriceSource, NewsSource):
    """
    An implementation of PriceSource and NewsSource using the vnstock library.
    This version propagates exceptions and uses safe data mapping.
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

            df_renamed = df.rename(columns={
                'time': 'trade_date',
                'value': 'turnover'
            })

            df_renamed['symbol'] = symbol
            df_renamed['trade_date'] = pd.to_datetime(df_renamed['trade_date']).dt.date

            return df_renamed.to_dict('records')

        except Exception as e:
            logger.error(f"vnstock failed to fetch OHLCV for {symbol}: {e}")
            raise

    def fetch_latest_news(self, symbol: str, days: int) -> List[dict]:
        """
        Fetches the latest news for a symbol using vnstock.
        Uses safe .get() access for mapping to prevent KeyErrors.
        Raises exceptions on failure.
        """
        try:
            logger.info(f"Fetching latest news for {symbol} for the last {days} days")
            news_list = self.stock.stock.news(symbol=symbol, page_size=30, page_num=1)

            if not news_list:
                logger.warning(f"No news data returned for {symbol}")
                return []

            processed_news = []
            cutoff_date = datetime.now() - timedelta(days=days)

            for article in news_list:
                published_at_str = article.get('published_at')
                if not published_at_str:
                    continue

                published_at = pd.to_datetime(published_at_str)
                if published_at < cutoff_date:
                    continue

                processed_article = {
                    'id': article.get('id', article.get('url', '')),
                    'url': article.get('url', ''),
                    'title': article.get('title', ''),
                    'source': article.get('source', 'vnstock'),
                    'text': article.get('description', ''),
                    'published_at': published_at,
                    'first_seen_time': datetime.now()
                }
                processed_news.append(processed_article)

            return processed_news

        except Exception as e:
            logger.error(f"vnstock failed to fetch news for {symbol}: {e}")
            raise
