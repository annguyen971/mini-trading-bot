#!/usr/bin/env python3
"""
Test script to explore vnstock macro data capabilities
Run: python test_vnstock_macro.py
"""

def test_vnindex():
    """Test getting VN-Index data"""
    print("\n=== Testing VN-Index ===")
    try:
        from vnstock import Quote
        quote = Quote(symbol='VNINDEX', source='VCI')
        df = quote.history(start='2025-11-01', end='2025-11-21', interval='1D')
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            print(f"✅ VN-Index latest close: {latest['close']}")
            print(f"   Date: {latest['time']}")
            return float(latest['close'])
        else:
            print("❌ No VN-Index data returned")
    except Exception as e:
        print(f"❌ VN-Index failed: {e}")
    return None

def test_exchange_rate():
    """Test getting USD/VND exchange rate"""
    print("\n=== Testing Exchange Rate ===")
    try:
        from vnstock.explorer.misc import vcb_exchange_rate
        from datetime import date
        df = vcb_exchange_rate(date=date.today().strftime('%Y-%m-%d'))
        if df is not None and not df.empty:
            # Find USD row
            usd_row = df[df['CurrencyCode'] == 'USD']
            if not usd_row.empty:
                buy_rate = usd_row.iloc[0]['Buy']
                sell_rate = usd_row.iloc[0]['Sell']
                avg_rate = (buy_rate + sell_rate) / 2
                print(f"✅ USD/VND: {avg_rate:.2f}")
                print(f"   Buy: {buy_rate}, Sell: {sell_rate}")
                return avg_rate
        print("❌ No exchange rate data")
    except Exception as e:
        print(f"❌ Exchange rate failed: {e}")
    return None

def test_gold_price():
    """Test getting gold price"""
    print("\n=== Testing Gold Price ===")
    try:
        from vnstock.explorer.misc import sjc_gold_price
        df = sjc_gold_price()
        if df is not None and not df.empty:
            print(f"✅ Gold prices available")
            print(df.head())
            return True
        print("❌ No gold price data")
    except Exception as e:
        print(f"❌ Gold price failed: {e}")
    return False

def test_fx_data():
    """Test international FX data for USD/VND via MSN"""
    print("\n=== Testing FX Data (MSN) ===")
    try:
        from vnstock import Vnstock
        fx = Vnstock().fx(symbol='USDVND', source='MSN')
        df = fx.quote.history(start='2025-11-01', end='2025-11-21', interval='1D')
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            print(f"✅ USD/VND (MSN): {latest['close']}")
            print(f"   Date: {latest['time']}")
            return float(latest['close'])
        else:
            print("❌ No FX data returned")
    except Exception as e:
        print(f"❌ FX data failed: {e}")
    return None

if __name__ == '__main__':
    print("=" * 60)
    print("VNStock Macro Data Capability Test")
    print("=" * 60)
    
    results = {
        'vnindex': test_vnindex(),
        'exchange_rate': test_exchange_rate(),
        'gold': test_gold_price(),
        'fx_msn': test_fx_data()
    }
    
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    for key, value in results.items():
        status = "✅ Available" if value else "❌ Not available"
        print(f"{key:15s}: {status}")
    
    print("\n💡 Recommendation:")
    if results['vnindex']:
        print("  - VN-Index: Use Quote(symbol='VNINDEX')")
    if results['exchange_rate']:
        print("  - USD/VND: Use vcb_exchange_rate() from vnstock.explorer.misc")
    if results['fx_msn']:
        print("  - USD/VND (Alt): Use Vnstock().fx(symbol='USDVND', source='MSN')")
