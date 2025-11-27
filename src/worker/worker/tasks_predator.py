"""
Predator Signal Scanner
========================
Scans all watchlist symbols daily and identifies active Predator scenarios.

This is the first gate in the Hybrid Sniper approach:
1. Predator scans for rule-based signals  
2. Only symbols with active scenarios proceed to ML validation
3. Both must agree before firing a signal
"""

import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict
import pandas as pd
from core_lib.db import get_db_connection
from core_lib.locks import get_advisory_lock
from core_lib.predator_scenarios import get_active_scenarios

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOCK_NAME = "predator_scan"


def fetch_symbol_data(conn, symbol: str, days: int = 30) -> pd.DataFrame:
    """
    Fetch OHLCV data for a symbol.
    
    Args:
        conn: Database connection
        symbol: Stock symbol
        days: Number of days of history to fetch
        
    Returns:
        DataFrame with columns: trade_date, open, high, low, close, volume
    """
    with conn.cursor() as cur:
        query = """
        SELECT 
            trade_date,
            open,
            high,
            low,
            close,
            volume
        FROM ta_silver
        WHERE symbol = %s
          AND trade_date >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY trade_date ASC
        """
        cur.execute(query, (symbol, days))
        rows = cur.fetchall()
        
        if not rows:
            return pd.DataFrame()
        
        df = pd.DataFrame(rows, columns=['trade_date', 'open', 'high', 'low', 'close', 'volume'])
        return df


def save_predator_signals(conn, symbol: str, signal_date: datetime.date, active_scenarios: List[Dict]):
    """
    Save active scenarios to predator_signals table.
    
    Args:
        conn: Database connection
        symbol: Stock symbol
        signal_date: Date of the signal
        active_scenarios: List of active scenario dicts
    """
    if not active_scenarios:
        return
    
    import json
    
    with conn.cursor() as cur:
        query = """
        INSERT INTO predator_signals (symbol, signal_date, active_scenarios, scenario_count, confidence)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (symbol, signal_date) 
        DO UPDATE SET
            active_scenarios = EXCLUDED.active_scenarios,
            scenario_count = EXCLUDED.scenario_count,
            confidence = EXCLUDED.confidence,
            created_at = NOW()
        """
        
        scenario_count = len(active_scenarios)
        avg_confidence = sum(s['strength'] for s in active_scenarios) / scenario_count
        
        cur.execute(query, (
            symbol,
            signal_date,
            json.dumps(active_scenarios),
            scenario_count,
            avg_confidence
        ))
    
    logger.info(f"✓ {symbol}: {scenario_count} scenarios active (confidence={avg_confidence:.2f})")


def run_predator_scan():
    """
    Main entry point for Predator scanner.
    
    Workflow:
    1. Fetch all symbols from watchlist
    2. For each symbol, fetch 30 days of price data
    3. Check all Predator scenarios
    4. Save active scenarios to database
    """
    logger.info("=" * 60)
    logger.info("Predator Scanner starting...")
    logger.info("=" * 60)
    
    conn = None
    
    try:
        conn = get_db_connection()
        
        # Acquire lock
        if not get_advisory_lock(conn, LOCK_NAME):
            logger.warning(f"Could not acquire lock '{LOCK_NAME}'. Another scanner is running.")
            return 0
        
        logger.info(f"✓ Acquired lock '{LOCK_NAME}'")
        
        # Fetch watchlist symbols
        with conn.cursor() as cur:
            cur.execute("SELECT symbol FROM symbol_watchlist WHERE is_active = true")
            symbols = [row[0] for row in cur.fetchall()]
        
        logger.info(f"Scanning {len(symbols)} symbols from watchlist...")
        
        signal_count = 0
        today = datetime.now().date()
        
        for symbol in symbols:
            try:
                # Fetch 30 days of data
                market_data = fetch_symbol_data(conn, symbol, days=30)
                
                if market_data.empty or len(market_data) < 25:
                    logger.debug(f"Skipping {symbol}: insufficient data")
                    continue
                
                # Check scenarios
                active_scenarios = get_active_scenarios(market_data)
                
                if active_scenarios:
                    save_predator_signals(conn, symbol, today, active_scenarios)
                    signal_count += 1
                
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                continue
        
        conn.commit()
        
        logger.info("=" * 60)
        logger.info(f"Predator scan complete: {signal_count}/{len(symbols)} symbols have active scenarios")
        logger.info("=" * 60)
        
        return 0
        
    except Exception as e:
        logger.exception(f"Predator scan failed: {e}")
        if conn:
            conn.rollback()
        return 1
        
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    import sys
    sys.exit(run_predator_scan())
