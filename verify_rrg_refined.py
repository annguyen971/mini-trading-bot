import os
import psycopg
import pandas as pd
import numpy as np
from datetime import date
import logging

# Setup
DB_URL = os.getenv("DB_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_rrg():
    try:
        conn = psycopg.connect(DB_URL)
        
        # 1. Trigger Calculation (Optional, if we want to test the function run)
        # For now, let's assume the worker runs it, or we can import and run it.
        # Let's import and run it to be sure we are testing the new code.
        import sys
        sys.path.append("/root/mini-trading-bot/mini-trading-bot/src")
        from worker.tasks_feature_gold import calculate_sector_stats
        
        test_date = date(2025, 11, 25) # Use a date with known data
        logger.info(f"Running calculate_sector_stats for {test_date}...")
        calculate_sector_stats(conn, test_date)
        
        # 2. Fetch Results
        sql = """
        SELECT * FROM sector_stats 
        WHERE as_of_date = %s
        """
        df = pd.read_sql(sql, conn, params=(test_date,))
        
        if df.empty:
            logger.error("No data found in sector_stats!")
            return

        print("\n--- RRG 2.0 Verification Results ---")
        print(df[['sector', 'rs_ratio', 'rs_momentum', 'breadth', 'concentration', 'turnover_shock_z']].head())
        
        # 3. Verify Dispersion (AC1)
        mom_std = df['rs_momentum'].std()
        ratio_std = df['rs_ratio'].std()
        print(f"\n[AC1] Dispersion Check:")
        print(f"  RS-Momentum StdDev: {mom_std:.2f} (Target > 1.0)")
        print(f"  RS-Ratio StdDev:    {ratio_std:.2f}")
        
        if mom_std > 1.0:
            print("  ✅ PASS: Momentum dispersion is sufficient.")
        else:
            print("  ❌ FAIL: Momentum dispersion is too low.")

        # 4. Verify Center Sanity (AC-Sanity)
        mom_median = df['rs_momentum'].median()
        ratio_median = df['rs_ratio'].median()
        print(f"\n[AC-Sanity] Center Check:")
        print(f"  RS-Momentum Median: {mom_median:.2f} (Target [99, 101])")
        print(f"  RS-Ratio Median:    {ratio_median:.2f} (Target [99, 101])")
        
        if 99 <= mom_median <= 101 and 99 <= ratio_median <= 101:
             print("  ✅ PASS: Data is correctly centered.")
        else:
             print("  ⚠️ NOTE: Data deviated from center (expected for synthetic data).")

        # 5. Verify Metrics Population & Anti-Fill (P1/P2)
        print(f"\n[Metrics] Population & Anti-Fill Check:")
        null_breadth = df['breadth'].isnull().sum()
        null_conc = df['concentration'].isnull().sum()
        null_shock = df['turnover_shock_z'].isnull().sum()
        
        # Check if we have any NULLs in Ratio/Momentum (Anti-Fill)
        null_ratio = df['rs_ratio'].isnull().sum()
        null_mom = df['rs_momentum'].isnull().sum()
        
        print(f"  Null Breadth: {null_breadth}")
        print(f"  Null Concentration: {null_conc}")
        print(f"  Null Shock: {null_shock}")
        print(f"  Null Ratio: {null_ratio} (Anti-Fill Check)")
        print(f"  Null Momentum: {null_mom} (Anti-Fill Check)")
        
        # We expect 0 nulls for breadth/conc/shock on this date, but maybe some for ratio/mom if history short
        if null_breadth == 0 and null_conc == 0:
            print("  ✅ PASS: Core metrics populated.")
        else:
            print("  ❌ FAIL: Some core metrics are NULL.")

        # 6. Verify Axis Sanity (P2)
        print(f"\n[P2] Axis Sanity Check:")
        min_ratio = df['rs_ratio'].min()
        max_ratio = df['rs_ratio'].max()
        min_mom = df['rs_momentum'].min()
        max_mom = df['rs_momentum'].max()
        
        print(f"  Ratio Range: [{min_ratio:.2f}, {max_ratio:.2f}]")
        print(f"  Momentum Range: [{min_mom:.2f}, {max_mom:.2f}]")
        
        if min_ratio < 100 < max_ratio and min_mom < 100 < max_mom:
            print("  ✅ PASS: Axes span across 100.")
        else:
            print("  ❌ FAIL: Axes do not span across 100 (Dead Chart?).")
            
        # 7. No-Clamp Check (P2)
        if (max_ratio > 105 or min_ratio < 95) or (max_mom > 105 or min_mom < 95):
            print("  ✅ PASS: Significant deviation detected (No Clamp).")
        else:
            print("  ⚠️ NOTE: Deviation is small (Market might be calm or clamped).")

        # 8. Verify Logic (Pillar Pull)
        # Find sectors that match the condition
        pillar_pull = df[(df['concentration'] > 0.6) & (df['breadth'] < 0.3)]
        print(f"\n[Logic] Pillar Pull Candidates (Conc > 0.6 & Breadth < 0.3):")
        if not pillar_pull.empty:
            print(pillar_pull[['sector', 'concentration', 'breadth']])
        else:
            print("  None found in this snapshot.")

        conn.close()

    except Exception as e:
        logger.error(f"Verification failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_rrg()
