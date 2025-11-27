import sys
import os
import random
from datetime import date, timedelta
import psycopg

# Add src/core_lib and src/api to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../src/core_lib'))
sys.path.append(os.path.join(os.path.dirname(__file__), '../src/api'))

from core_lib.db import get_db_connection
from api.deja_vu import find_similar_days

def run_validation():
    conn = get_db_connection()
    try:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # 1. Get list of available symbols and dates (2022-2024)
            print("Fetching test candidates...")
            cur.execute("""
                SELECT symbol, effective_date 
                FROM features_gold_serving 
                WHERE effective_date BETWEEN '2022-01-01' AND '2024-01-01'
                AND hmm_state IS NOT NULL
                ORDER BY RANDOM() 
                LIMIT 100
            """)
            candidates = cur.fetchall()
            
            results = []
            
            print(f"Running backtest on {len(candidates)} samples...")
            
            for i, cand in enumerate(candidates):
                symbol = cand['symbol']
                ref_date = cand['effective_date']
                
                # Run Deja Vu
                output = find_similar_days(symbol, ref_date=ref_date, top_k=10)
                
                if "error" in output:
                    print(f"Skipping {symbol} on {ref_date}: {output['error']}")
                    continue
                    
                verdict = output['analytics']['verdict']
                
                # Get Actual Return T+5
                cur.execute("""
                    SELECT close 
                    FROM ta_silver 
                    WHERE symbol = %s AND trade_date > %s 
                    ORDER BY trade_date ASC 
                    LIMIT 5
                """, (symbol, ref_date))
                future_rows = cur.fetchall()
                
                actual_return = 0.0
                if future_rows:
                    # Get T+0 price
                    cur.execute("SELECT close FROM ta_silver WHERE symbol = %s AND trade_date = %s", (symbol, ref_date))
                    p0_row = cur.fetchone()
                    if p0_row:
                        p0 = p0_row['close']
                        p5 = future_rows[-1]['close']
                        if p0:
                            actual_return = (p5 - p0) / p0
                
                # Check correctness
                is_correct = False
                if verdict == "BULLISH" and actual_return > 0:
                    is_correct = True
                elif verdict == "BEARISH" and actual_return < 0:
                    is_correct = True
                
                results.append({
                    "symbol": symbol,
                    "date": ref_date,
                    "verdict": verdict,
                    "actual_return": actual_return,
                    "is_correct": is_correct
                })
                
                if (i+1) % 10 == 0:
                    print(f"Processed {i+1}/100...")

            # Calculate Metrics
            directional_samples = [r for r in results if r['verdict'] in ['BULLISH', 'BEARISH']]
            correct_samples = [r for r in directional_samples if r['is_correct']]
            
            accuracy = 0.0
            if directional_samples:
                accuracy = len(correct_samples) / len(directional_samples)
                
            print("\n=== VALIDATION RESULTS ===")
            print(f"Total Samples: {len(results)}")
            print(f"Directional Samples (Bull/Bear): {len(directional_samples)}")
            print(f"Correct Predictions: {len(correct_samples)}")
            print(f"Directional Accuracy: {accuracy*100:.2f}%")
            
            if accuracy > 0.55:
                print("PASSED: Accuracy > 55%")
            else:
                print("FAILED: Accuracy <= 55%")

    except Exception as e:
        print(f"Validation Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_validation()
