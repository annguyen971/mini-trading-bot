#!/usr/bin/env python3
"""
Manual TA Backfill Script
==========================
Backfills 6 months of price data for all active symbols in watchlist.
"""

import os
import sys
from datetime import datetime, timedelta
import time

# Add paths
sys.path.insert(0, '/root/mini-trading-bot/mini-trading-bot/src')

from vnstock import *
import psycopg2

# Database connection
DB_URL = os.environ.get('DB_URL', 'postgresql://hunter:hunter2023@localhost:5432/hunter')
conn = psycopg2.connect(DB_URL)

# Get watchlist
print("Fetching active symbols...")
with conn.cursor() as cur:
    cur.execute("SELECT symbol FROM symbol_watchlist WHERE is_active = true ORDER BY symbol")
    symbols = [row[0] for row in cur.fetchall()]

print(f"Found {len(symbols)} symbols: {', '.join(symbols)}")

# Initialize vnstock
stock = Vnstock()

# Backfill parameters
end_date = datetime.now()
start_date = end_date - timedelta(days=180)  # 6 months
start_str = start_date.strftime('%Y-%m-%d')
end_str = end_date.strftime('%Y-%m-%d')

print(f"\nBackfilling from {start_str} to {end_str}\n")

# Process each symbol
for i, symbol in enumerate(symbols, 1):
    print(f"[{i}/{len(symbols)}] Processing {symbol}...")
    
    try:
        # Fetch OHLCV data using vnstock v3 API
        df = stock_historical_data(
            symbol=symbol,
            start_date=start_str,
            end_date=end_str,
            resolution='1D',
            type='stock'
        )
        
        if df is None or df.empty:
            print(f"  ⚠️  No data for {symbol}")
            continue
            
        # Insert into ta_silver
        inserted = 0
        with conn.cursor() as cur:
            for _, row in df.iterrows():
                try:
                    cur.execute("""
                        INSERT INTO ta_silver (symbol, trade_date, open, high, low, close, volume, turnover, as_of_time)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (symbol, trade_date) DO NOTHING
                    """, (
                        symbol,
                        row['time'],
                        row['open'],
                        row['high'],
                        row['low'],
                        row['close'],
                        row['volume'],
                        row.get('turnover', row['volume'] * row['close'])  # Approximate if not available
                    ))
                    inserted += 1
                except Exception as e:
                    print(f"    Error on row: {e}")
                    
        conn.commit()
        print(f"  ✓ Inserted {inserted} records")
        time.sleep(0.5)  # Rate limiting
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        conn.rollback()

print("\n✓ TA Backfill Complete!")
conn.close()
