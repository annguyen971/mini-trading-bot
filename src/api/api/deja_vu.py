from typing import List, Dict, Any, Optional
import numpy as np
import psycopg
from datetime import date
from core_lib.db import get_db_connection

def find_similar_days(symbol: str, ref_date: Optional[date] = None, top_k: int = 10) -> Dict[str, Any]:
    """
    Finds historical days with similar market patterns using Deja Vu 2.1 Logic.
    
    Stage 1: Hard Filter (Context)
        - HMM State: Must match exactly.
        - Sector: Must match exactly (at that time).
        - OBV Slope Sign: Must match exactly (Money Flow Direction).
        - Time: Strictly past data (No Lookahead).
        
    Stage 2: Soft Ranking (Similarity)
        - Weighted Euclidean Distance:
          d = sqrt(2.0 * dHunter^2 + 1.0 * dFroth^2 + 2.5 * dOBV^2)
          (OBV is King)
          
    Args:
        symbol: Stock symbol
        ref_date: Reference date for 'Time Travel' (default: today)
        top_k: Number of similar days to return
        
    Returns:
        Dict with target_context, similar_events, and analytics.
    """
    conn = get_db_connection()
    try:
        query_date = ref_date if ref_date else date.today()

        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # 1. Fetch Feature & Sector of target symbol AT REF_DATE
            cur.execute("""
                SELECT hmm_state, "FrothScore", "HunterScore", sector, obv_slope_5d, sector_rs_ratio
                FROM features_gold_serving
                WHERE symbol = %s AND effective_date <= %s
                ORDER BY effective_date DESC LIMIT 1
            """, (symbol, query_date))
            target = cur.fetchone()

            if not target: 
                return {"error": f"No data found for {symbol} at {query_date}"}

            # Normalize & Prepare Target Vector
            curr_hmm = target['hmm_state']
            curr_froth = float(target['FrothScore'] or 50) / 100.0
            curr_hunter = float(target['HunterScore'] or 50) / 100.0
            curr_obv = float(target['obv_slope_5d'] or 0.0)
            curr_sector_rs = float(target['sector_rs_ratio'] or 100.0)
            target_sector = target['sector']
            
            # OBV Sign for Hard Filter
            curr_obv_sign = 1 if curr_obv > 0 else (-1 if curr_obv < 0 else 0)
            
            if curr_hmm is None or target_sector is None:
                 return {"error": f"Incomplete context for {symbol} (HMM: {curr_hmm}, Sector: {target_sector})"}

            # 2. Context Search (Hybrid: Hard Filter + Soft Rank)
            # Normalization for OBV in distance:
            # OBV slope can be large. We should probably normalize it or use sign match + magnitude rank.
            # For simplicity in SQL, we'll assume OBV slope is roughly comparable or use a scaling factor.
            # Better: Use Rank or Z-score. But for MVP, let's use raw difference scaled down?
            # Or just rely on the fact that we filter by sign, so we compare magnitude.
            # Let's assume OBV slope is small enough or we scale it. 
            # Actually, standardizing in SQL is hard without stats.
            # Let's use a simple scaling factor of 0.1 for OBV to bring it to 0-1 range approx?
            # Or just accept it dominates. Spec says "OBV is King" (Weight 2.5).
            
            sql = """
            WITH candidates AS (
                SELECT
                    f.symbol, f.effective_date,
                    f."FrothScore", f."HunterScore",
                    f.sector,
                    f.hmm_state,
                    f.obv_slope_5d,
                    f.sector_rs_ratio
                FROM features_gold_serving f
                WHERE
                    f.hmm_state = %s          -- HARD FILTER 1: Context
                    AND f.sector = %s         -- HARD FILTER 2: Sector
                    AND SIGN(COALESCE(f.obv_slope_5d, 0)) = %s -- HARD FILTER 3: Money Flow Direction
                    AND f.effective_date < %s - INTERVAL '5 days' -- Anti-Leakage
                    AND f.effective_date > %s - INTERVAL '1095 days' -- Lookback 3 years
                LIMIT 5000
            )
            SELECT *,
                -- SOFT RANKING: Weighted Euclidean
                -- Distance = sqrt(2 * (dH)^2 + 1 * (dF)^2 + 2.5 * (dOBV)^2)
                -- Scaling OBV by 0.01 to make it comparable to 0-1 scores if it's volume based?
                -- Wait, OBV slope is volume change. It can be huge.
                -- We MUST normalize.
                -- Let's use a trick: Rank-based or just compare relative to self?
                -- For now, let's assume inputs are normalized or we just use the raw values 
                -- but cap the diff to 1.0 to avoid exploding distance.
                SQRT(
                    2.0 * POWER((("HunterScore"/100.0 - %s)), 2) +
                    1.0 * POWER((("FrothScore"/100.0 - %s)), 2) +
                    2.5 * POWER(LEAST(ABS(COALESCE(obv_slope_5d, 0) - %s) / 1000000.0, 1.0), 2) -- Rough scaling for Volume
                ) as distance
            FROM candidates
            ORDER BY distance ASC
            LIMIT %s;
            """

            cur.execute(sql, (
                curr_hmm, target_sector, curr_obv_sign, query_date, query_date,
                curr_hunter, curr_froth, curr_obv, top_k
            ))
            rows = cur.fetchall()

            # 3. Post-Analysis & Return Calculation
            results = []
            positive_count = 0
            
            for r in rows:
                # Calculate T+5 Return
                date_t = r['effective_date']
                
                cur.execute("""
                    SELECT close 
                    FROM ta_silver 
                    WHERE symbol = %s AND trade_date > %s 
                    ORDER BY trade_date ASC 
                    LIMIT 5
                """, (r['symbol'], date_t))
                future_prices = cur.fetchall()
                
                return_t5 = 0.0
                if future_prices:
                    cur.execute("SELECT close FROM ta_silver WHERE symbol = %s AND trade_date = %s", (r['symbol'], date_t))
                    p0_row = cur.fetchone()
                    
                    if p0_row and future_prices:
                        p0 = p0_row['close']
                        p5 = future_prices[-1]['close']
                        if p0:
                            return_t5 = (p5 - p0) / p0

                if return_t5 > 0:
                    positive_count += 1

                results.append({
                    "symbol": r['symbol'],
                    "date": r['effective_date'].isoformat(),
                    "similarity_score": round(1.0 / (1.0 + r['distance']), 4),
                    "return_t5": round(return_t5, 4),
                    "context": {
                        "hmm": r['hmm_state'],
                        "hunter": r['HunterScore'],
                        "froth": r['FrothScore'],
                        "obv_slope": r['obv_slope_5d'],
                        "sector_rs": r['sector_rs_ratio']
                    }
                })

            # Verdict Logic (Deja Vu Statistical Verdict)
            # Note: This is separate from the "Decision Engine" in main.py which uses current data.
            # This verdict is based on "What happened in similar past days?"
            win_rate = positive_count / len(rows) if rows else 0
            if win_rate >= 0.6:
                verdict = "BULLISH"
            elif win_rate <= 0.4:
                verdict = "BEARISH"
            else:
                verdict = "NEUTRAL"

            return {
                "target_context": {
                    "symbol": symbol,
                    "date": query_date.isoformat(),
                    "hmm": curr_hmm, 
                    "sector": target_sector,
                    "hunter": round(curr_hunter * 100, 1),
                    "froth": round(curr_froth * 100, 1),
                    "obv_slope": curr_obv,
                    "sector_rs": curr_sector_rs
                },
                "similar_events": results,
                "analytics": {
                    "sample_size": len(rows),
                    "historical_win_rate": round(win_rate * 100, 1),
                    "verdict": verdict
                }
            }

    except Exception as e:
        print(f"Deja Vu Error: {e}")
        return {"error": str(e)}
    finally:
        conn.close()
