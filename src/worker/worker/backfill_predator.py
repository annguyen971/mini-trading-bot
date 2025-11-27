"""
Backfill Predator Signals
=========================
Backfills predator_signals table for the past year to enable model training.
Optimized to calculate indicators on full history vectorially.
"""

import os
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from core_lib.db import get_db_connection
from core_lib.predator_scenarios import Sniper_RSI_Divergence, Sniper_Vol_Breakout
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def backfill_symbol(conn, symbol: str, days: int = 365):
    """Backfill signals for a single symbol."""
    with conn.cursor() as cur:
        # Calculate start date in Python to avoid SQL interval syntax issues
        start_date = datetime.now() - timedelta(days=days + 60)
        
        # Fetch history
        query = """
        SELECT trade_date, open, high, low, close, volume
        FROM ta_silver
        WHERE symbol = %s
          AND trade_date >= %s
        ORDER BY trade_date ASC
        """
        cur.execute(query, (symbol, start_date))
        rows = cur.fetchall()
        
        if not rows:
            return
        
        df = pd.DataFrame(rows, columns=['trade_date', 'open', 'high', 'low', 'close', 'volume'])
        logger.info(f"Fetched {len(df)} rows for {symbol}")
        
        # Pre-calculate indicators
        # 1. RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi_14'] = 100 - (100 / (1 + rs))
        
        # 2. Bollinger Bands
        sma = df['close'].rolling(window=20).mean()
        std = df['close'].rolling(window=20).std()
        df['bb_upper'] = sma + (std * 2.0)
        df['vol_ma20'] = df['volume'].rolling(window=20).mean()
        
        # Instantiate scenarios
        # Use ALL_SCENARIOS from core_lib to ensure we pick up new ones
        from core_lib.predator_scenarios import ALL_SCENARIOS
        
        signals_to_insert = []
        
        # Iterate through history (skipping warmup)
        for i in range(30, len(df)):
            row = df.iloc[i]
            signal_date = row['trade_date']
            
            # Slice for scenarios that need history (like divergence)
            # Divergence needs last 5 days
            recent_df = df.iloc[i-5:i+1] # window of 6 days
            
            active_scenarios = []
            
            # Slice for check_signal (needs 25-30 days)
            slice_df = df.iloc[max(0, i-30):i+1].copy()
            
            # Debug logging for first symbol, first few iterations
            if i < 35: 
                logger.info(f"Checking {symbol} at {signal_date}")
                # logger.info(f"Close: {row['close']}, Vol: {row['volume']}, VolMA: {row['vol_ma20']}")
                # logger.info(f"RSI: {row['rsi_14']}, BB Upper: {row['bb_upper']}")

            for scenario in ALL_SCENARIOS:
                if scenario.check_signal(slice_df):
                    active_scenarios.append({
                        "name": scenario.name,
                        "strength": scenario.get_strength(slice_df)
                    })
            
            if active_scenarios:
                signals_to_insert.append((
                    symbol,
                    signal_date,
                    json.dumps(active_scenarios),
                    len(active_scenarios),
                    sum(s['strength'] for s in active_scenarios) / len(active_scenarios)
                ))
            
            if active_scenarios:
                signals_to_insert.append((
                    symbol,
                    signal_date,
                    json.dumps(active_scenarios),
                    len(active_scenarios),
                    sum(s['strength'] for s in active_scenarios) / len(active_scenarios)
                ))
        
        if signals_to_insert:
            cur.executemany("""
                INSERT INTO predator_signals (symbol, signal_date, active_scenarios, scenario_count, confidence)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (symbol, signal_date) DO NOTHING
            """, signals_to_insert)
            logger.info(f"Backfilled {len(signals_to_insert)} signals for {symbol}")

def run_backfill():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT symbol FROM symbol_watchlist WHERE is_active = true")
            symbols = [row[0] for row in cur.fetchall()]
        
        logger.info(f"Backfilling for {len(symbols)} symbols: {symbols}")
        
        for i, symbol in enumerate(symbols):
            try:
                backfill_symbol(conn, symbol)
                if i % 10 == 0:
                    conn.commit()
            except Exception as e:
                logger.error(f"Error backfilling {symbol}: {e}")
                conn.rollback()
        
        conn.commit()
        logger.info("Backfill complete.")
        
    finally:
        conn.close()

if __name__ == "__main__":
    run_backfill()
