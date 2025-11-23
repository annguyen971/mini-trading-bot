"""
Macro Data Source - VNStock Implementation
Fetches Vietnamese macroeconomic indicators using vnstock library
"""

from datetime import datetime, date, timedelta
from typing import Dict, Optional
import logging

from vnstock import Quote
from vnstock.explorer.misc import vcb_exchange_rate

logger = logging.getLogger(__name__)


class MacroDataSource:
    """
    Fetches macro economic data from VNStock.
    
    Available metrics:
    - VNIndex: Current Vietnam stock index value
    - USDVND: USD/VND exchange rate from Vietcombank
    
    Note: CPI, GDP, Policy Rate require manual updates or government data sources
    as they are not available via vnstock in real-time.
    """
    
    def __init__(self, source='VCI'):
        """
        Initialize macro data source.
        
        Args:
            source: VNStock data source ('VCI', 'TCBS', 'MSN')
        """
        self.source = source
        self.name = f"vnstock-{source}"
    
    def fetch_vnindex(self) -> Optional[float]:
        """
        Fetch current VN-Index value.
        
        Returns:
            Latest VN-Index close value, or None if failed
        """
        try:
            quote = Quote(symbol='VNINDEX', source=self.source)
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
            
            df = quote.history(start=start_date, end=end_date, interval='1D')
            
            if df is None or df.empty:
                logger.warning("VN-Index: No data returned")
                return None
            
            latest = df.iloc[-1]
            value = float(latest['close'])
            logger.info(f"VN-Index fetched: {value} (date: {latest['time']})")
            return value
            
        except Exception as e:
            logger.error(f"Failed to fetch VN-Index: {e}")
            return None
    
    def fetch_usdvnd(self) -> Optional[float]:
        """
        Fetch USD/VND exchange rate from Vietcombank.
        
        Returns:
            Average USD/VND rate, or None if failed
        """
        try:
            today = date.today().strftime('%Y-%m-%d')
            df = vcb_exchange_rate(date=today)
            
            if df is None or df.empty:
                logger.warning("USD/VND: No exchange rate data returned")
                return None
            
            # Find USD row
            usd_row = df[df['currency_code'] == 'USD']
            if usd_row.empty:
                logger.warning("USD/VND: USD not found in exchange rate data")
                return None
            
            # Get buy and sell rates
            buy_str = usd_row.iloc[0]['buy _transfer']  # Note: space in column name
            sell_str = usd_row.iloc[0]['sell']
            
            # Parse strings (remove commas)
            buy = float(buy_str.replace(',', ''))
            sell = float(sell_str.replace(',', ''))
            
            # Calculate average
            avg = (buy + sell) / 2
            
            logger.info(f"USD/VND fetched: {avg:.2f} (buy: {buy}, sell: {sell})")
            return avg
            
        except Exception as e:
            logger.error(f"Failed to fetch USD/VND: {e}")
            return None
    
    def fetch_all_metrics(self) -> Dict[str, float]:
        """
        Fetch all available macro metrics.
        
        Returns:
            Dictionary of {metric_name: value}
        """
        metrics = {}
        
        # Fetch VN-Index
        vnindex = self.fetch_vnindex()
        if vnindex is not None:
            metrics['VNIndex'] = vnindex
        
        # Fetch USD/VND
        usdvnd = self.fetch_usdvnd()
        if usdvnd is not None:
            metrics['USDVND'] = usdvnd
        
        logger.info(f"Fetched {len(metrics)} macro metrics from {self.name}")
        return metrics


def refresh_macro_data_from_vnstock():
    """
    Main entry point to refresh macro data.
    Called by scheduled jobs (cron or worker).
    
    Updates macro_clean table with fresh data from VNStock.
    """
    from core_lib.db import get_db_connection
    
    source = MacroDataSource(source='VCI')
    metrics = source.fetch_all_metrics()
    
    if not metrics:
        logger.error("No macro metrics fetched - all sources failed")
        return False
    
    # Update database
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            for metric_name, value in metrics.items():
                cur.execute("""
                    INSERT INTO macro_clean (metric_name, value, last_updated, effective_date)
                    VALUES (%s, %s, NOW(), CURRENT_DATE)
                    ON CONFLICT (metric_name)
                    DO UPDATE SET 
                        value = EXCLUDED.value,
                        last_updated = NOW(),
                        effective_date = CURRENT_DATE
                """, (metric_name, value))
            
            conn.commit()
            logger.info(f"✅ Updated {len(metrics)} macro metrics in database")
            return True
            
    except Exception as e:
        logger.error(f"Failed to update macro_clean table: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    # For testing: python -m scraper.macro_sources
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("Macro Data Refresh Test")
    print("=" * 60)
    
    success = refresh_macro_data_from_vnstock()
    
    if success:
        print("\n✅ Macro data refresh completed successfully")
    else:
        print("\n❌ Macro data refresh failed")
