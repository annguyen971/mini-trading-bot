#!/usr/bin/env python3
"""
One-time script to update macro_clean with real data from VNStock
Run inside scraper container: python /tmp/update_macro.py
"""

from datetime import datetime, date, timedelta
import sys

print("=" * 60)
print("Macro Data Update Script")
print("=" * 60)

# 1. Fetch VN-Index
print("\n[1/4] Fetching VN-Index from VNStock...")
try:
    from vnstock import Quote
    quote = Quote(symbol='VNINDEX', source='VCI')
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    df = quote.history(start=start_date, end=end_date, interval='1D')
    
    if df is None or df.empty:
        print("   ❌ No VN-Index data")
        vnindex = None
    else:
        vnindex = float(df.iloc[-1]['close'])
        print(f"   ✅ VN-Index: {vnindex}")
except Exception as e:
    print(f"   ❌ Error: {e}")
    vnindex = None

# 2. Fetch USD/VND
print("\n[2/4] Fetching USD/VND from VCB...")
try:
    from vnstock.explorer.misc import vcb_exchange_rate
    today = date.today().strftime('%Y-%m-%d')
    df = vcb_exchange_rate(date=today)
    usd_row = df[df['currency_code'] == 'USD']
    
    if usd_row.empty:
        print("   ❌ USD not found")
        usdvnd = None
    else:
        buy = float(usd_row.iloc[0]['buy _transfer'].replace(',', ''))
        sell = float(usd_row.iloc[0]['sell'].replace(',', ''))
        usdvnd = (buy + sell) / 2
        print(f"   ✅ USD/VND: {usdvnd:.2f}")
except Exception as e:
    print(f"   ❌ Error: {e}")
    usdvnd = None

# 3. Prepare metrics
print("\n[3/4] Preparing metrics...")
metrics = {}
if vnindex is not None:
    metrics['VNIndex'] = vnindex
if usdvnd is not None:
    metrics['USDVND'] = usdvnd

if not metrics:
    print("   ❌ No metrics to update")
    sys.exit(1)

print(f"   ✅ {len(metrics)} metrics ready")

# 4. Update database
print("\n[4/4] Updating database...")
try:
    sys.path.insert(0, '/app')
    from core_lib.db import get_db_connection
    
    conn = get_db_connection()
    with conn.cursor() as cur:
        for metric_name, value in metrics.items():
            print(f"   Updating {metric_name} = {value}")
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
    conn.close()
    print(f"   ✅ Database updated successfully!")
    
    # Verify
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT metric_name, value, last_updated FROM macro_clean WHERE metric_name IN ('VNIndex', 'USDVND') ORDER BY metric_name")
        rows = cur.fetchall()
        print("\n" + "=" * 60)
        print("Verification - Current Values in Database:")
        print("=" * 60)
        for row in rows:
            print(f"  {row[0]:15s} = {row[1]:10.2f}  (updated: {row[2]})")
    conn.close()
    
except Exception as e:
    print(f"   ❌ Database error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("✅ MACRO DATA UPDATE COMPLETED")
print("=" * 60)
