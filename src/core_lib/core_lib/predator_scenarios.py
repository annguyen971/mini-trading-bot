from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

# Scenario Constants
SCENARIO_DISTRIBUTION_CLIMAX = "DISTRIBUTION_CLIMAX"
SCENARIO_STEALTH_ACCUMULATION = "STEALTH_ACCUMULATION"
SCENARIO_SHAKEOUT = "SHAKEOUT"
SCENARIO_UPTHRUST = "UPTHRUST"
SCENARIO_NEUTRAL = "NEUTRAL"

def detect_scenario(features: Dict[str, float]) -> str:
    """
    Detects market scenario based on deterministic rules (Story 6.1).
    
    Args:
        features: Dict containing:
            - hmm_state: int (0=Accumulation, 1=Breakout, 2=Euphoria, 3=Distribution)
            - hype_crowd_z: float (Z-score of crowd sentiment)
            - hype_elitist_z: float (Z-score of elite sentiment)
            - price_change: float (Daily % change, e.g., 0.05 for 5%)
            - vol_rel: float (Volume relative to 20-day avg)
            
    Returns:
        Scenario tag string
    """
    hmm = features.get('hmm_state', -1)
    crowd = features.get('hype_crowd_z', 0.0)
    elite = features.get('hype_elitist_z', 0.0)
    price_chg = features.get('price_change', 0.0)
    vol_rel = features.get('vol_rel', 1.0)
    
    # Rule 1: DISTRIBUTION_CLIMAX (Xả hàng)
    # HMM in [Euphoria(2), Breakout(1)] AND Crowd >= 1.5 AND Elite <= -0.5
    if hmm in [1, 2] and crowd >= 1.5 and elite <= -0.5:
        return SCENARIO_DISTRIBUTION_CLIMAX
        
    # Rule 2: STEALTH_ACCUMULATION (Gom hàng)
    # HMM in [Accumulation(0)] AND Crowd <= -1.5 AND Elite >= 0.5
    if hmm == 0 and crowd <= -1.5 and elite >= 0.5:
        return SCENARIO_STEALTH_ACCUMULATION
        
    # Rule 3: SHAKEOUT (Rũ bỏ)
    # Price giảm > 3% AND Vol > 1.5 TB 20 phiên AND Elite > 0
    if price_chg < -0.03 and vol_rel > 1.5 and elite > 0:
        return SCENARIO_SHAKEOUT
        
    # Rule 4: UPTHRUST (Kéo xả)
    # Price tăng > 3% AND Vol < 0.8 TB 20 phiên AND Elite < 0
    if price_chg > 0.03 and vol_rel < 0.8 and elite < 0:
        return SCENARIO_UPTHRUST
        
    return SCENARIO_NEUTRAL

def calculate_pain_levels(history_df: pd.DataFrame, bins: int = 20) -> Dict[str, float]:
    """
    Calculates 'Trapped Price' (Liquidity Map) based on Volume Profile.
    
    Args:
        history_df: DataFrame with columns ['close', 'volume']
        bins: Number of price bins
        
    Returns:
        Dict with 'price' (Trapped Price) and 'vol_ratio' (Volume at that price / Total Volume)
    """
    default_res = {'price': 0.0, 'vol_ratio': 0.0}
    
    if history_df.empty:
        return default_res
        
    # Use last 60 days as per spec
    df = history_df.tail(60).copy()
    
    if len(df) < 5:
        return {'price': float(df['close'].mean()), 'vol_ratio': 0.0} if not df.empty else default_res
        
    price_min = df['close'].min()
    price_max = df['close'].max()
    
    if price_min == price_max:
        return {'price': float(price_min), 'vol_ratio': 1.0}
        
    # Create bins
    try:
        df['bin'] = pd.cut(df['close'], bins=bins, include_lowest=True)
        
        # Sum volume per bin
        vol_profile = df.groupby('bin', observed=True)['volume'].sum()
        total_vol = vol_profile.sum()
        
        if vol_profile.empty or total_vol == 0:
             return {'price': float(df['close'].mean()), 'vol_ratio': 0.0}

        # Find bin with max volume
        max_vol_bin = vol_profile.idxmax()
        max_vol = vol_profile.max()
        
        # Return mid-point of that bin and ratio
        return {
            'price': float(max_vol_bin.mid),
            'vol_ratio': float(max_vol / total_vol)
        }
    except Exception as e:
        print(f"Error calculating pain levels: {e}")
        return {'price': float(df['close'].mean()), 'vol_ratio': 0.0}
