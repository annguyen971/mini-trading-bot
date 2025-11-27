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

import argparse

# Add paths - Try to detect environment
if os.path.exists('/app/worker'):
    sys.path.insert(0, '/app')
else:
    sys.path.insert(0, '/root/mini-trading-bot/mini-trading-bot/src')

from vnstock import *
import psycopg2

# Parse arguments
parser = argparse.ArgumentParser(description='Manual TA Backfill')
parser.add_argument('--days', type=int, default=1095, help='Days to backfill (default: 1095 = 3 years)')
parser.add_argument('--symbols', type=str, help='Comma-separated list of symbols (optional)')
args = parser.parse_args()

# Database connection
DB_URL = os.environ.get('DB_URL', 'postgresql://hunter:hunter2023@localhost:5432/hunter')
conn = psycopg2.connect(DB_URL)

# Get watchlist
if args.symbols:
    symbols = [s.strip() for s in args.symbols.split(',')]
    print(f"Using provided {len(symbols)} symbols")
else:
    print("Fetching active symbols from DB...")
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM symbol_watchlist WHERE is_active = true ORDER BY symbol")
        symbols = [row[0] for row in cur.fetchall()]
    print(f"Found {len(symbols)} active symbols")

# Initialize vnstock
stock = Vnstock()

# Backfill parameters
end_date = datetime.now()
start_date = end_date - timedelta(days=args.days)
start_str = start_date.strftime('%Y-%m-%d')
end_str = end_date.strftime('%Y-%m-%d')

print(f"\nBackfilling {len(symbols)} symbols from {start_str} to {end_str}\n")

# Process each symbol
for i, symbol in enumerate(symbols, 1):
    print(f"[{i}/{len(symbols)}] Processing {symbol}...")
    
    try:
        # Fetch OHLCV data using vnstock v3 API (Quote class)
        # Default source 'VCI' is reliable
        quote = Quote(symbol=symbol, source='VCI')
        df = quote.history(start=start_str, end=end_str, interval='1D')
        
        if df is None or df.empty:
            print(f"  ⚠️  No data for {symbol}")
            continue
            
        # Rename columns to match DB schema
        # vnstock returns: time, open, high, low, close, volume
        if 'time' in df.columns:
            df = df.rename(columns={'time': 'trade_date'})
            
        # Insert into ta_silver
        inserted = 0
        with conn.cursor() as cur:
            for _, row in df.iterrows():
                try:
                    # Calculate turnover if missing (approximate)
                    vol = int(row['volume'])
                    close = float(row['close'])
                    turnover = row.get('value', vol * close) # Some sources return 'value'
                    
                    cur.execute("""
                        INSERT INTO ta_silver (symbol, trade_date, open, high, low, close, volume, turnover, as_of_time)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (symbol, trade_date) DO NOTHING
                    """, (
                        symbol,
                        row['trade_date'],
                        row['open'],
                        row['high'],
                        row['low'],
                        close,
                        vol,
                        turnover
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
