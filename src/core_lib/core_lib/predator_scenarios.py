"""
Predator Scenarios - Hybrid Sniper Edition
===========================================
Provides rule-based signal generators for high-precision entry points.

Each scenario is a deterministic rule that identifies specific market conditions.
Only symbols with active scenarios proceed to ML validation.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import pandas as pd
import numpy as np


# ==============================================================================
# Abstract Base Class
# ==============================================================================

class AbstractScenario(ABC):
    """
    Base class for all Predator scenarios.
    
    Each scenario must implement:
    - check_signal: Returns True if scenario conditions are met
    - get_strength: Returns confidence score (0.0 to 1.0)
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique scenario identifier"""
        pass
    
    @abstractmethod
    def check_signal(self, market_data: pd.DataFrame) -> bool:
        """
        Check if scenario conditions are met.
        
        Args:
            market_data: DataFrame with columns:
                - close, high, low, open, volume
                - rsi_14 (if needed)
                - bb_upper, bb_lower (if needed)
                
        Returns:
            True if signal is active, False otherwise
        """
        pass
    
    @abstractmethod
    def get_strength(self, market_data: pd.DataFrame) -> float:
        """
        Calculate scenario strength/confidence.
        
        Returns:
            Float between 0.0 and 1.0
        """
        pass


# ==============================================================================
# High-Precision Scenarios
# ==============================================================================

class Sniper_RSI_Divergence(AbstractScenario):
    """
    Detects Bullish RSI Divergence:
    - Price makes lower low
    - RSI makes higher low
    - Volume above 20-day MA (confirmation)
    
    This is a mean-reversion signal with high precision.
    """
    
    @property
    def name(self) -> str:
        return "Sniper_RSI_Divergence"
    
    def check_signal(self, market_data: pd.DataFrame) -> bool:
        """Check for bullish divergence"""
        if len(market_data) < 25:  # Need enough history
            return False
        
        df = market_data.tail(25).copy()
        
        # Calculate RSI if not present
        if 'rsi_14' not in df.columns:
            df['rsi_14'] = self._calculate_rsi(df['close'], period=14)
        
        # Calculate volume MA
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        
        # Get last 5 days for divergence detection
        recent = df.tail(5)
        
        if len(recent) < 5:
            return False
        
        # Find price lows
        price_vals = recent['close'].values
        price_low_1 = price_vals[0]
        price_low_2 = price_vals[-1]
        
        # Find RSI lows
        rsi_vals = recent['rsi_14'].values
        rsi_low_1 = rsi_vals[0]
        rsi_low_2 = rsi_vals[-1]
        
        # Bullish Divergence conditions
        price_lower_low = price_low_2 < price_low_1
        rsi_higher_low = rsi_low_2 > rsi_low_1
        rsi_oversold = rsi_low_1 < 35  # Relaxed from 30
        volume_confirm = recent['volume'].iloc[-1] > recent['vol_ma20'].iloc[-1]
        
        return price_lower_low and rsi_higher_low and rsi_oversold and volume_confirm
    
    def get_strength(self, market_data: pd.DataFrame) -> float:
        """Calculate divergence strength"""
        if not self.check_signal(market_data):
            return 0.0
        
        df = market_data.tail(5)
        
        # Strength based on RSI recovery magnitude
        rsi_diff = df['rsi_14'].iloc[-1] - df['rsi_14'].iloc[0]
        strength = min(rsi_diff / 20.0, 1.0)  # Normalize to 0-1
        
        return max(0.5, strength)  # Minimum 0.5 if signal is active
    
    @staticmethod
    def _calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI indicator"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi


class Sniper_Vol_Breakout(AbstractScenario):
    """
    Detects Volume Breakout with Bollinger Band confirmation:
    - Price closes above Upper Bollinger Band
    - Volume > 200% of 20-day average (explosive move)
    - Confirms strong momentum surge
    """
    
    @property
    def name(self) -> str:
        return "Sniper_Vol_Breakout"
    
    def check_signal(self, market_data: pd.DataFrame) -> bool:
        """Check for volume breakout"""
        if len(market_data) < 25:
            return False
        
        df = market_data.tail(25).copy()
        
        # Calculate Bollinger Bands if not present
        if 'bb_upper' not in df.columns:
            bb = self._calculate_bollinger_bands(df['close'], period=20, std=2.0)
            df['bb_upper'] = bb['upper']
            df['bb_lower'] = bb['lower']
        
        # Calculate volume average
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        
        latest = df.iloc[-1]
        
        # Breakout conditions
        price_above_bb = latest['close'] > latest['bb_upper']
        volume_surge = latest['volume'] > (latest['vol_ma20'] * 1.2)  # Relaxed to 120% threshold
        
        return price_above_bb and volume_surge
    
    def get_strength(self, market_data: pd.DataFrame) -> float:
        """Calculate breakout strength"""
        if not self.check_signal(market_data):
            return 0.0
        
        df = market_data.tail(25)
        latest = df.iloc[-1]
        
        # Strength based on volume surge magnitude
        vol_ratio = latest['volume'] / latest['vol_ma20']
        strength = min((vol_ratio - 1.2) / 3.8, 1.0)  # 120-500% surge maps to 0-1
        
        return max(0.6, strength)  # Minimum 0.6 if signal is active
    
    @staticmethod
    def _calculate_bollinger_bands(prices: pd.Series, period: int = 20, std: float = 2.0) -> Dict[str, pd.Series]:
        """Calculate Bollinger Bands"""
        sma = prices.rolling(window=period).mean()
        rolling_std = prices.rolling(window=period).std()
        upper = sma + (rolling_std * std)
        lower = sma - (rolling_std * std)
        return {'upper': upper, 'lower': lower, 'middle': sma}


class Mean_Reversion_BB(AbstractScenario):
    """
    Detects Mean Reversion from Lower Bollinger Band:
    - Price closes below Lower Band (Oversold extension)
    - Green Candle (Close > Open) indicates potential reversal
    - High Recall strategy
    """
    
    @property
    def name(self) -> str:
        return "Mean_Reversion_BB"
    
    def check_signal(self, market_data: pd.DataFrame) -> bool:
        """Check for mean reversion - FLOODGATE: MFI filter removed"""
        if len(market_data) < 25:
            return False
        
        df = market_data.tail(25).copy()
        
        # Calculate Bollinger Bands if not present
        if 'bb_lower' not in df.columns:
            sma = df['close'].rolling(window=20).mean()
            rolling_std = df['close'].rolling(window=20).std()
            df['bb_lower'] = sma - (rolling_std * 2.0)
        
        latest = df.iloc[-1]
        
        # Conditions (RELAXED - removed MFI filter)
        # 1. Close below Lower Band
        # 2. Green Candle (potential reversal)
        
        below_band = latest['close'] < latest['bb_lower']
        green_candle = latest['close'] > latest['open']
        
        return below_band and green_candle
    
    def get_strength(self, market_data: pd.DataFrame) -> float:
        if not self.check_signal(market_data):
            return 0.0
        return 0.7  # Moderate confidence baseline


class Trend_Pullback(AbstractScenario):
    """
    FLOODGATE: Detects pullback in uptrend (HIGH RECALL)
    - Close > SMA50 (uptrend confirmation)
    - RSI_3 < 30 (short-term pullback)
    
    Classic setup that appears frequently. Let ML decide which pullbacks are worth buying.
    """
    
    @property
    def name(self) -> str:
        return "Trend_Pullback"
    
    def check_signal(self, market_data: pd.DataFrame) -> bool:
        """Check for trend pullback"""
        if len(market_data) < 55:  # Need 50 + buffer
            return False
        
        df = market_data.tail(55).copy()
        
        # Calculate SMA50 if not present
        if 'sma_50' not in df.columns:
            df['sma_50'] = df['close'].rolling(window=50).mean()
        
        # Calculate RSI_3 if not present
        if 'rsi_3' not in df.columns:
            df['rsi_3'] = self._calculate_rsi(df['close'], period=3)
        
        latest = df.iloc[-1]
        
        # Conditions
        in_uptrend = latest['close'] > latest['sma_50']
        pullback = latest['rsi_3'] < 30
        
        return in_uptrend and pullback
    
    def get_strength(self, market_data: pd.DataFrame) -> float:
        if not self.check_signal(market_data):
            return 0.0
        return 0.6  # Moderate baseline
    
    @staticmethod
    def _calculate_rsi(prices: pd.Series, period: int = 3) -> pd.Series:
        """Calculate RSI indicator"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi


class RSI_Oversold_Simple(AbstractScenario):
    """
    FLOODGATE: Simplified RSI oversold (replaces complex divergence logic)
    - RSI_14 < 35
    
    Let ML learn the price patterns, we just flag oversold conditions.
    """
    
    @property
    def name(self) -> str:
        return "RSI_Oversold_Simple"
    
    def check_signal(self, market_data: pd.DataFrame) -> bool:
        if len(market_data) < 20:
            return False
        
        df = market_data.tail(20).copy()
        
        # Calculate RSI if not present
        if 'rsi_14' not in df.columns:
            df['rsi_14'] = Sniper_RSI_Divergence._calculate_rsi(df['close'], period=14)
        
        return df['rsi_14'].iloc[-1] < 35  # Slightly less strict than 30
    
    def get_strength(self, market_data: pd.DataFrame) -> float:
        if not self.check_signal(market_data):
            return 0.0
        return 0.5  # Lower confidence since it's very simple


class Sniper_RSI_Oversold(AbstractScenario):
    """
    Detects Simple RSI Oversold:
    - RSI < 30
    - High Recall strategy to ensure sufficient training data
    """
    
    @property
    def name(self) -> str:
        return "Sniper_RSI_Oversold"
    
    def check_signal(self, market_data: pd.DataFrame) -> bool:
        if len(market_data) < 25: return False
        df = market_data.tail(25).copy()
        
        # Calculate RSI if not present (reuse static method from sibling)
        if 'rsi_14' not in df.columns:
            df['rsi_14'] = Sniper_RSI_Divergence._calculate_rsi(df['close'], period=14)
            
        return df['rsi_14'].iloc[-1] < 30
    
    def get_strength(self, market_data: pd.DataFrame) -> float:
        if not self.check_signal(market_data): return 0.0
        return 0.6


# ==============================================================================
# Scenario Registry
# ==============================================================================

ALL_SCENARIOS = [
    # Original scenarios (kept for diversity)
    Sniper_RSI_Divergence(),
    Sniper_Vol_Breakout(),
    Mean_Reversion_BB(),  # MFI filter removed
    Sniper_RSI_Oversold(),
    # FLOODGATE scenarios (high recall)
    Trend_Pullback(),
    RSI_Oversold_Simple(),
]


def get_active_scenarios(market_data: pd.DataFrame) -> List[Dict[str, any]]:
    """
    Check all scenarios and return active ones.
    
    Args:
        market_data: DataFrame with OHLCV data
        
    Returns:
        List of dicts: [{"name": "...", "strength": 0.8}, ...]
    """
    active = []
    
    for scenario in ALL_SCENARIOS:
        if scenario.check_signal(market_data):
            strength = scenario.get_strength(market_data)
            active.append({
                "name": scenario.name,
                "strength": strength
            })
    
    return active


# ==============================================================================
# Legacy Functions (Kept for Compatibility)
# ==============================================================================

# Scenario Constants
SCENARIO_DISTRIBUTION_CLIMAX = "DISTRIBUTION_CLIMAX"
SCENARIO_STEALTH_ACCUMULATION = "STEALTH_ACCUMULATION"
SCENARIO_SHAKEOUT = "SHAKEOUT"
SCENARIO_UPTHRUST = "UPTHRUST"
SCENARIO_NEUTRAL = "NEUTRAL"

def detect_scenario(features: Dict[str, float]) -> str:
    """
    DEPRECATED: Legacy scenario detection.
    Use get_active_scenarios() instead.
    """
    hmm = features.get('hmm_state', -1)
    crowd = features.get('hype_crowd_z', 0.0)
    elite = features.get('hype_elitist_z', 0.0)
    price_chg = features.get('price_change', 0.0)
    vol_rel = features.get('vol_rel', 1.0)
    
    if hmm in [1, 2] and crowd >= 1.5 and elite <= -0.5:
        return SCENARIO_DISTRIBUTION_CLIMAX
    if hmm == 0 and crowd <= -1.5 and elite >= 0.5:
        return SCENARIO_STEALTH_ACCUMULATION
    if price_chg > 0.03 and vol_rel < 0.8 and elite < 0:
        return SCENARIO_UPTHRUST
    return SCENARIO_NEUTRAL


def calculate_pain_levels(df: pd.DataFrame) -> Dict[str, float]:
    """
    Calculate the price level with the highest volume (Point of Control).
    Used for 'Liquidity Map' / 'Trapped Zone'.
    """
    if df.empty:
        return {'price': 0.0, 'vol_ratio': 0.0}
        
    # Simple Volume Profile: Bin prices and sum volume
    # Use 20 bins
    try:
        price_min = df['close'].min()
        price_max = df['close'].max()
        
        if price_min == price_max:
            return {'price': float(price_min), 'vol_ratio': 1.0}
            
        bins = np.linspace(price_min, price_max, 21)
        # Digitize returns bin indices (1-based)
        bin_indices = np.digitize(df['close'], bins)
        
        # Sum volume per bin
        vol_by_bin = {}
        total_vol = df['volume'].sum()
        
        if total_vol == 0:
             return {'price': float(df['close'].iloc[-1]), 'vol_ratio': 0.0}
             
        for i, vol in zip(bin_indices, df['volume']):
            vol_by_bin[i] = vol_by_bin.get(i, 0) + vol
            
        # Find max volume bin
        max_bin = max(vol_by_bin, key=vol_by_bin.get)
        max_vol = vol_by_bin[max_bin]
        
        # Approximate price of that bin (midpoint)
        # bin i corresponds to interval [bins[i-1], bins[i]]
        # Note: digitize returns i such that bins[i-1] <= x < bins[i]
        # If x > bins[-1], returns len(bins).
        
        idx = max_bin - 1
        if idx < 0: idx = 0
        if idx >= len(bins) - 1: idx = len(bins) - 2
        
        price_level = (bins[idx] + bins[idx+1]) / 2.0
        
        return {
            'price': float(price_level),
            'vol_ratio': float(max_vol / total_vol)
        }
    except Exception as e:
        print(f"Error in calculate_pain_levels: {e}")
        return {'price': 0.0, 'vol_ratio': 0.0}
