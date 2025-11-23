from typing import List, Dict, Any
import numpy as np
import psycopg
from core_lib.db import get_db_connection

def find_similar_days(symbol: str, current_features: Dict[str, Any], top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Finds historical days with similar market patterns (Story 6.4).
    Uses Euclidean distance on [hmm_state, FrothScore, HunterScore].
    Strictly searches past data (date < today).
    
    Args:
        symbol: Stock symbol
        current_features: Dict with keys 'hmm_state', 'FrothScore', 'HunterScore'
        top_k: Number of similar days to return
        
    Returns:
        List of dicts with date, similarity, return_t5
    """
    conn = get_db_connection()
    try:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            # Fetch historical features
            # We join with ta_silver to get 'close' price for return calculation
            cur.execute("""
                SELECT f.effective_date, f.hmm_state, f."FrothScore", f."HunterScore", t.close
                FROM features_gold_serving f
                JOIN ta_silver t ON f.symbol = t.symbol AND f.effective_date = t.trade_date
                WHERE f.symbol = %s AND f.effective_date < CURRENT_DATE
                ORDER BY f.effective_date DESC
                LIMIT 500
            """, (symbol,))
            rows = cur.fetchall()
            
            if not rows:
                return []

            # Prepare feature matrix X
            # Handle None values with defaults
            X_list = []
            valid_rows = []
            
            for r in rows:
                hmm = r['hmm_state'] if r['hmm_state'] is not None else -1
                froth = r['FrothScore'] if r['FrothScore'] is not None else 50.0
                hunter = r['HunterScore'] if r['HunterScore'] is not None else 50.0
                
                X_list.append([float(hmm), float(froth), float(hunter)])
                valid_rows.append(r)
                
            if not X_list:
                return []
                
            X = np.array(X_list)
            
            # Current vector
            current_vec = np.array([
                float(current_features.get('hmm_state', -1)),
                float(current_features.get('FrothScore', 50.0)),
                float(current_features.get('HunterScore', 50.0))
            ])
            
            # Calculate Euclidean distances
            # dist = sqrt(sum((x - y)^2))
            dists = np.linalg.norm(X - current_vec, axis=1)
            
            # Get indices of k nearest neighbors
            # argsort returns indices that would sort the array
            nearest_indices = dists.argsort()[:top_k]
            
            results = []
            for idx in nearest_indices:
                row = valid_rows[idx]
                dist = dists[idx]
                
                # Calculate forward return (T+5)
                date_t = row['effective_date']
                cur.execute("""
                    SELECT close 
                    FROM ta_silver 
                    WHERE symbol = %s AND trade_date > %s 
                    ORDER BY trade_date ASC 
                    LIMIT 5
                """, (symbol, date_t))
                future_rows = cur.fetchall()
                
                return_t5 = 0.0
                if future_rows:
                    price_t = row['close']
                    # Get the furthest available price up to T+5
                    price_future = future_rows[-1]['close']
                    if price_t and price_future:
                        return_t5 = (price_future - price_t) / price_t
                
                results.append({
                    "date": row['effective_date'].isoformat(),
                    "similarity_score": float(1.0 / (1.0 + dist)), # Normalize to 0-1 (1 is identical)
                    "return_t5": float(return_t5),
                    "scenario_context": f"HMM: {row['hmm_state']}, Hunter: {row['HunterScore']}"
                })
                
            return results

    except Exception as e:
        print(f"Deja Vu error: {e}")
        return []
    finally:
        conn.close()
