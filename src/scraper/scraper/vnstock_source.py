import pandas as pd
from datetime import datetime, timedelta
from typing import List
import logging

# Import vnstock classes theo documentation chính thống
from vnstock import Quote

from core_lib.data_source import PriceSource, NewsSource

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VnStockSource(PriceSource, NewsSource):
    """
    An implementation of PriceSource and NewsSource using the vnstock library.
    Refactored to use direct functional calls (vnstock standard).
    """
    def __init__(self):
        # Initialize with VCI as default source (reliable)
        self.source = 'VCI'

    def get_ohlcv(self, symbol: str, start_date: str, end_date: str) -> List[dict]:
        """
        Fetches historical OHLCV data using vnstock.
        """
        try:
            logger.info(f"Fetching OHLCV for {symbol} from {start_date} to {end_date}")
            
            # Use Quote class per vnstock documentation
            quote = Quote(symbol=symbol, source=self.source)
            df = quote.history(start=start_date, end=end_date, interval='1D')

            if df is None or df.empty:
                logger.warning(f"No OHLCV data returned for {symbol}")
                return []

            # Chuẩn hóa tên cột cho khớp với hệ thống của bạn
            # vnstock trả về: time, open, high, low, close, volume
            # TAData expects: symbol, trade_date, open, high, low, close, volume (int), value/turnover (float)
            df_renamed = df.rename(columns={
                'time': 'trade_date'
            })

            df_renamed['symbol'] = symbol
            # Đảm bảo trade_date là object date
            df_renamed['trade_date'] = pd.to_datetime(df_renamed['trade_date']).dt.date
            
            # Add 'value' field (turnover) - keep volume and add value
            # value/turnover is typically volume * close (as approximation)
            if 'volume' not in df_renamed.columns:
                logger.warning(f"'volume' column missing from vnstock data for {symbol}")
                df_renamed['volume'] = 0
            
            # Calculate turnover value (volume * close)
            df_renamed['value'] = df_renamed['volume'] * df_renamed['close']
            
            # Ensure volume is int
            df_renamed['volume'] = df_renamed['volume'].astype(int)

            # Convert sang list dict
            return df_renamed.to_dict('records')

        except Exception as e:
            logger.error(f"vnstock failed to fetch OHLCV for {symbol}: {e}")
            # Return list rỗng để không crash worker, hoặc raise nếu muốn retry
            # Ở đây raise để worker biết là lỗi
            raise

    def fetch_latest_news(self, symbol: str, days: int) -> List[dict]:
        """
        Fetches the latest news for a symbol using vnstock company news.
        """
        try:
            logger.info(f"Fetching latest news for {symbol}")
            
            # Use vnstock Company class to get news
            from vnstock import Company
            
            company = Company(symbol=symbol, source='TCBS')
            
            # Fetch news - vnstock returns DataFrame
            news_df = company.news(page_size=20)  # Get last 20 articles
            
            if news_df is None or news_df.empty:
                logger.warning(f"No news data returned for {symbol}")
                return []
            
            logger.info(f"News columns: {news_df.columns.tolist()}")
            if not news_df.empty:
                logger.info(f"First news row: {news_df.iloc[0].to_dict()}")
            
            # Calculate date threshold
            threshold_date = datetime.now() - timedelta(days=days)
            
            # Filter by publish date if available
            if 'publishDate' in news_df.columns:
                news_df['publishDate'] = pd.to_datetime(news_df['publishDate'])
                news_df = news_df[news_df['publishDate'] >= threshold_date]
            
            # Convert to list of dicts with standardized schema
            news_list = []
            for _, row in news_df.iterrows():
                article = {
                    'id': str(row.get('id', row.get('newsID', ''))),
                    'source': row.get('source', 'TCBS'),
                    'url': f"https://tcinvest.tcbs.com.vn/news/{row.get('id')}" if row.get('id') else "",
                    'title': row.get('title', ''),
                    'text': row.get('content', row.get('description', row.get('title', ''))), # Fallback to title
                    'published_at': row.get('publishDate', datetime.now()) if pd.notna(row.get('publishDate')) else datetime.now(),
                    'first_seen_time': datetime.now(),
                }
                news_list.append(article)
            
            logger.info(f"Successfully fetched {len(news_list)} news articles for {symbol}")
            return news_list

        except ImportError:
            logger.error("vnstock Company class not available. Install vnstock3>=3.0.0")
            return []
        except Exception as e:
            logger.error(f"vnstock failed to fetch news for {symbol}: {e}")
            # News errors can be skipped, return empty
            return []