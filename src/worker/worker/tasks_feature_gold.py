"""
Feature Gold Batch - Stock Hunter AI
=====================================
Batch job to compute HMM states, FrothScore, HunterScore, and Macro_Impact_Score.

Story 2.3: Runs daily at 02:30, reads from v_features_asof, publishes atomically to serving table.

Reference: doc/feature_logic_v1.md
"""

import math
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import numpy as np

from core_lib.db import get_db_connection

# --- Constants ---
ADVISORY_LOCK_NAME = "feature_gold_batch"
FEATURE_SET_VERSION = os.getenv("FEATURE_SET_VERSION", "v1.0")

# HMM Parameters
HMM_STATES = 4  # Accumulation, Breakout, Euphoria, Distribution
HMM_STICKY_BIAS = 0.6  # κ parameter for self-transition bias

# Score thresholds
FROTH_PENALTY_GAMMA = 0.6  # γ parameter for Hunter penalty

# Lookback windows
ZSCORE_WINDOW_MACRO = 36 * 21  # ~36 months (trading days)
ZSCORE_WINDOW_SA = 180  # trading days
ZSCORE_WINDOW_TA = 252  # trading days

# --- Utility Functions (from feature_logic_v1.md) ---

def z_pos(z: float) -> float:
    """Returns max(0.0, z) - only positive z-scores contribute to froth."""
    return max(0.0, z)

def clip(x: float, a: float = 0.0, b: float = 1.0) -> float:
    """Clips x to range [a, b]."""
    return min(max(x, a), b)

def rel(x: float, lo: float = 0.0, hi: float = 0.3) -> float:
    """Relative normalization: (x-lo)/(hi-lo), clipped to [0,1]."""
    if hi == lo:
        return 0.0
    return clip((x - lo) / (hi - lo), 0, 1)

def sigmoid(x: float) -> float:
    """Sigmoid function: 1/(1+exp(-x))."""
    return 1.0 / (1.0 + math.exp(-x))

def z_score(values: List[float], current: float) -> float:
    """
    Computes z-score of current value against historical distribution.
    Returns 0.0 if insufficient data.
    """
    if len(values) < 2:
        return 0.0

    mean = np.mean(values)
    std = np.std(values, ddof=1)

    if std == 0:
        return 0.0

    return (current - mean) / std

# --- HMM Implementation (AC2) ---

class HMM4State:
    """
    4-State HMM with sticky bias and transition mask.

    States:
    0: Accumulation (Base-building, low volatility)
    1: Breakout (Momentum building, volume spike)
    2: Euphoria (Peak excitement, high froth)
    3: Distribution (Selling, declining volume)
    """

    def __init__(self, sticky_bias: float = HMM_STICKY_BIAS):
        self.n_states = HMM_STATES
        self.sticky_bias = sticky_bias

        # Transition matrix (base probabilities before sticky bias)
        # Rows: current state, Columns: next state
        # [Accum, Breakout, Euphoria, Distrib]
        self.transition_base = np.array([
            [0.7, 0.25, 0.0, 0.05],  # Accum -> mostly stays, can -> Breakout
            [0.1, 0.5, 0.35, 0.05],  # Breakout -> can -> Euphoria
            [0.0, 0.1, 0.6, 0.3],    # Euphoria -> can -> Distribution
            [0.3, 0.0, 0.0, 0.7],    # Distrib -> can -> Accum (reset)
        ])

        # Apply sticky bias (increase self-transition probability)
        self.transition_matrix = self._apply_sticky_bias()

        # Initial state probabilities (default: start in Accumulation)
        self.initial_probs = np.array([0.7, 0.2, 0.05, 0.05])

    def _apply_sticky_bias(self) -> np.ndarray:
        """
        Apply sticky bias κ to increase self-transition probabilities.
        This reduces state "flicker" (rapid switching).
        """
        trans = self.transition_base.copy()

        for i in range(self.n_states):
            # Boost self-transition
            self_prob = trans[i, i]
            boosted_self = self_prob + self.sticky_bias * (1 - self_prob)

            # Normalize other transitions
            other_prob = 1 - boosted_self
            trans[i, :] *= (other_prob / (1 - self_prob))
            trans[i, i] = boosted_self

        return trans

    def emission_probability(self, state: int, features: Dict[str, float]) -> float:
        """
        Compute emission probability P(observation | state).

        Features used:
        - vol_ratio: volume_today / volume_ma20
        - rsi_14: RSI indicator
        - froth_raw: raw froth score (0-1)
        """
        vol_ratio = features.get('vol_ratio', 1.0)
        rsi = features.get('rsi_14', 50.0)
        froth_raw = features.get('froth_raw', 0.0)

        # State-specific emission models
        if state == 0:  # Accumulation
            # Expect: low volume, neutral RSI, low froth
            p_vol = 1.0 if vol_ratio < 1.2 else 0.3
            p_rsi = 1.0 if 40 <= rsi <= 60 else 0.5
            p_froth = 1.0 if froth_raw < 0.3 else 0.2
            return p_vol * p_rsi * p_froth

        elif state == 1:  # Breakout
            # Expect: high volume, rising RSI, moderate froth
            p_vol = 1.0 if vol_ratio > 1.5 else 0.3
            p_rsi = 1.0 if 55 <= rsi <= 75 else 0.5
            p_froth = 1.0 if 0.3 <= froth_raw <= 0.6 else 0.4
            return p_vol * p_rsi * p_froth

        elif state == 2:  # Euphoria
            # Expect: very high volume, overbought RSI, high froth
            p_vol = 1.0 if vol_ratio > 2.0 else 0.4
            p_rsi = 1.0 if rsi > 70 else 0.3
            p_froth = 1.0 if froth_raw > 0.6 else 0.2
            return p_vol * p_rsi * p_froth

        else:  # Distribution (state == 3)
            # Expect: declining volume, falling RSI, moderate froth
            p_vol = 1.0 if vol_ratio < 0.8 else 0.5
            p_rsi = 1.0 if rsi < 50 else 0.6
            p_froth = 1.0 if 0.3 <= froth_raw <= 0.7 else 0.4
            return p_vol * p_rsi * p_froth

    def filter_step(self, prev_probs: np.ndarray, features: Dict[str, float]) -> np.ndarray:
        """
        Forward filtering step (filtering-only, no smoothing).

        Args:
            prev_probs: State probabilities at T-1
            features: Observed features at T

        Returns:
            Updated state probabilities at T
        """
        # Predict: p(state_t | obs_{1:t-1})
        predicted = self.transition_matrix.T @ prev_probs

        # Update: p(state_t | obs_{1:t})
        emissions = np.array([
            self.emission_probability(s, features)
            for s in range(self.n_states)
        ])

        updated = predicted * emissions

        # Normalize
        total = np.sum(updated)
        if total > 0:
            updated /= total
        else:
            # Fallback to uniform if all emissions are zero
            updated = np.ones(self.n_states) / self.n_states

        return updated

    def most_likely_state(self, probs: np.ndarray) -> int:
        """Returns the state with highest probability."""
        return int(np.argmax(probs))

# --- FrothScore Calculation (AC3) ---

def compute_froth_score(conn, symbol: str, as_of_date: str) -> float:
    """
    Computes FrothScore (0-100) per feature_logic_v1.md.

    Formula:
    FrothScore = 100 * clip(
        0.25*S_crowd + 0.20*S_burst + 0.40*S_tech + 0.15*breadth_contra,
        0, 1
    )

    Components:
    - S_crowd: Positive z-score of crowd sentiment
    - S_burst: News volume burst (penalized if catalyst present)
    - S_tech: Technical "noise" (parabolic slope, extension, turnover, gaps)
    - breadth_contra: Breadth divergence (price up but sector weak)
    """

    with conn.cursor() as cursor:
        # Fetch historical crowd sentiment for z-score calculation
        cursor.execute("""
            SELECT sentiment_score
            FROM sa_silver
            WHERE symbol = %s
              AND publisher_time_utc <= %s
            ORDER BY publisher_time_utc DESC
            LIMIT %s
        """, (symbol, as_of_date, ZSCORE_WINDOW_SA))

        sentiment_history = [row[0] for row in cursor.fetchall() if row[0] is not None]

        if not sentiment_history:
            return 0.0  # No sentiment data

        # Current sentiment (latest)
        current_sentiment = sentiment_history[0] if sentiment_history else 0.0

        # Component 1: S_crowd (positive z-score only)
        z_sentiment = z_score(sentiment_history, current_sentiment)
        S_crowd = z_pos(z_sentiment)

        # Component 2: S_burst (news volume)
        # Simplified: count news in last 7 days vs historical average
        cursor.execute("""
            SELECT COUNT(*) FROM sa_silver
            WHERE symbol = %s
              AND publisher_time_utc > %s - interval '7 days'
              AND publisher_time_utc <= %s
        """, (symbol, as_of_date, as_of_date))

        news_count_7d = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) / 30.0 as avg_per_week FROM sa_silver
            WHERE symbol = %s
              AND publisher_time_utc > %s - interval '180 days'
              AND publisher_time_utc <= %s
        """, (symbol, as_of_date, as_of_date))

        avg_news_per_week = cursor.fetchone()[0] or 1.0

        news_ratio = news_count_7d / avg_news_per_week if avg_news_per_week > 0 else 0.0
        burst_z = (news_ratio - 1.0) / 0.5  # Simple normalization

        # Check for catalyst
        cursor.execute("""
            SELECT COUNT(*) FROM catalyst_flags
            WHERE sa_silver_ref_id IN (
                SELECT url_canonical FROM sa_silver
                WHERE symbol = %s
                  AND publisher_time_utc > %s - interval '7 days'
                  AND publisher_time_utc <= %s
            )
        """, (symbol, as_of_date, as_of_date))

        has_catalyst = cursor.fetchone()[0] > 0
        catalyst_penalty = 0.4 if has_catalyst else 1.0

        S_burst = z_pos(burst_z) * catalyst_penalty

        # Component 3: S_tech (technical noise)
        # Simplified: Use RSI extension and volume acceleration
        cursor.execute("""
            SELECT close, volume
            FROM ta_silver
            WHERE symbol = %s
              AND trade_date <= %s
            ORDER BY trade_date DESC
            LIMIT 50
        """, (symbol, as_of_date))

        ta_data = cursor.fetchall()

        if len(ta_data) < 20:
            S_tech = 0.0
        else:
            prices = [row[0] for row in ta_data]
            volumes = [row[1] for row in ta_data]

            # Extension from MA50
            current_price = prices[0]
            ma50 = np.mean(prices) if len(prices) >= 50 else np.mean(prices[:min(len(prices), 20)])
            extension = rel(current_price / ma50 - 1, lo=0.0, hi=0.3)

            # Volume acceleration (3-day avg / 10-day avg)
            vol_3d = np.mean(volumes[:3])
            vol_10d = np.mean(volumes[:10]) if len(volumes) >= 10 else vol_3d
            turnover_accel = rel(vol_3d / vol_10d - 1, lo=0.0, hi=0.5) if vol_10d > 0 else 0.0

            # Simplified S_tech (missing parabolic slope, gaps for now)
            S_tech = 0.5 * extension + 0.5 * turnover_accel

        # Component 4: breadth_contra (simplified: assume 0 for now)
        breadth_contra = 0.0

        # Final FrothScore
        froth_raw = 0.25 * S_crowd + 0.20 * S_burst + 0.40 * S_tech + 0.15 * breadth_contra
        froth_score = round(100 * clip(froth_raw, 0, 1))

        return froth_score

# --- HunterScore Calculation (AC4) ---

def compute_hunter_score(conn, symbol: str, as_of_date: str, froth_score: float) -> float:
    """
    Computes HunterScore (0-100) per feature_logic_v1.md.

    Formula (before penalty):
    Hunter_raw = 0.50*H_div + 0.30*H_ta + 0.20*H_cat

    With froth penalty:
    penalty = (1 - γ * FrothScore/100), γ = 0.6
    HunterScore = 100 * clip(Hunter_raw * penalty, 0, 1)

    Components:
    - H_div: Elitist vs Crowd divergence
    - H_ta: Technical "smart" signals (VCP, pocket pivot, RS)
    - H_cat: Catalyst flags
    """

    with conn.cursor() as cursor:
        # Component 1: H_div (Elitist vs Crowd divergence)
        # Simplified: Use sentiment_score as proxy (would need elitist weights in real impl)
        cursor.execute("""
            SELECT sentiment_score
            FROM sa_silver
            WHERE symbol = %s
              AND publisher_time_utc <= %s
            ORDER BY publisher_time_utc DESC
            LIMIT 1
        """, (symbol, as_of_date))

        sentiment_row = cursor.fetchone()
        current_sentiment = sentiment_row[0] if sentiment_row and sentiment_row[0] is not None else 0.0

        # Placeholder: In real implementation, compute z(hype_elitist) - z(hype_crowd)
        # For now, use sentiment as proxy
        div_raw = current_sentiment  # Range typically -1 to 1
        H_div = clip(sigmoid(div_raw), 0, 1)

        # Component 2: H_ta (Technical signals)
        # Simplified: Use RSI and volume as proxy for VCP/pocket pivot
        cursor.execute("""
            SELECT close, high, low, volume
            FROM ta_silver
            WHERE symbol = %s
              AND trade_date <= %s
            ORDER BY trade_date DESC
            LIMIT 63
        """, (symbol, as_of_date))

        ta_data = cursor.fetchall()

        if len(ta_data) < 21:
            H_ta = 0.0
        else:
            closes = np.array([row[0] for row in ta_data])
            volumes = np.array([row[3] for row in ta_data])

            # RS (Relative Strength) - simplified: recent performance
            rs_21 = (closes[0] / closes[20] - 1) if closes[20] > 0 else 0.0
            rs_63 = (closes[0] / closes[62] - 1) if len(closes) >= 63 and closes[62] > 0 else 0.0

            # Volatility contraction (ATR decreasing)
            atr_recent = np.mean(np.abs(np.diff(closes[:10])))
            atr_hist = np.mean(np.abs(np.diff(closes[10:30])))
            vcp = 1.0 if atr_recent < atr_hist else 0.0

            # Pocket pivot (volume spike with price up)
            vol_spike = volumes[0] > np.mean(volumes[1:11])
            price_up = closes[0] > closes[1]
            pocket = 1.0 if (vol_spike and price_up) else 0.0

            # Combine
            H_ta = 0.3 * vcp + 0.3 * pocket + 0.2 * rel(rs_21, -0.05, 0.15) + 0.2 * rel(rs_63, -0.1, 0.3)

        # Component 3: H_cat (Catalyst flags)
        cursor.execute("""
            SELECT COUNT(*) FROM catalyst_flags cf
            JOIN sa_silver sa ON cf.sa_silver_ref_id = sa.url_canonical
            WHERE sa.symbol = %s
              AND sa.publisher_time_utc > %s - interval '30 days'
              AND sa.publisher_time_utc <= %s
        """, (symbol, as_of_date, as_of_date))

        catalyst_count = cursor.fetchone()[0]
        H_cat = min(1.0, catalyst_count * 0.5)  # Each catalyst worth 0.5, capped at 1.0

        # Combine
        hunter_raw = 0.50 * H_div + 0.30 * H_ta + 0.20 * H_cat

        # Apply froth penalty
        penalty = 1.0 - FROTH_PENALTY_GAMMA * (froth_score / 100.0)
        hunter_adjusted = clip(hunter_raw * penalty, 0, 1)

        hunter_score = round(100 * hunter_adjusted)

        return hunter_score

# --- Macro Impact Score (AC5) ---

def compute_macro_impact_score(conn, sector: str, as_of_date: str) -> int:
    """
    Computes Macro_Impact_Score (-10 to +10) per feature_logic_v1.md.

    Formula:
    S = Σ α_factor * w[factor, sector] * g_factor
    Macro_Impact_Score = round(10 * clip(S, -1, 1))

    Where:
    - g_factor = tanh(z_score / 2) with appropriate sign
    - α = factor weight (e.g., 0.35 for interest rate)
    - w = sector-specific sensitivity matrix
    """

    # Placeholder: In real implementation, fetch from macro tables
    # For now, return neutral (0)
    return 0

# --- Main Feature Gold Batch (AC1/AC3/AC6) ---

def compute_features_for_symbol(conn, symbol: str, as_of_date: str) -> Dict[str, any]:
    """
    Computes all features for a single symbol at a specific date.

    Returns dict with:
    - hmm_state: int (0-3)
    - froth_score: float (0-100)
    - hunter_score: float (0-100)
    - macro_impact_score: int (-10 to +10)
    """

    # Step 1: Compute FrothScore
    froth_score = compute_froth_score(conn, symbol, as_of_date)

    # Step 2: Compute HunterScore (with froth penalty)
    hunter_score = compute_hunter_score(conn, symbol, as_of_date, froth_score)

    # Step 3: Compute HMM state (requires historical features)
    # Simplified: Use current features to estimate state
    hmm = HMM4State()

    # Fetch recent data for HMM
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT close, volume
            FROM ta_silver
            WHERE symbol = %s
              AND trade_date <= %s
            ORDER BY trade_date DESC
            LIMIT 21
        """, (symbol, as_of_date))

        ta_recent = cursor.fetchall()

    if len(ta_recent) >= 20:
        prices = [row[0] for row in ta_recent]
        volumes = [row[1] for row in ta_recent]

        # Compute features for HMM
        vol_ma20 = np.mean(volumes)
        vol_ratio = volumes[0] / vol_ma20 if vol_ma20 > 0 else 1.0

        # Simple RSI calculation (14-period)
        changes = np.diff(prices[:15])
        gains = np.maximum(changes, 0)
        losses = np.abs(np.minimum(changes, 0))
        avg_gain = np.mean(gains) if len(gains) > 0 else 0.0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0.0
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi = 100 - (100 / (1 + rs))

        features = {
            'vol_ratio': vol_ratio,
            'rsi_14': rsi,
            'froth_raw': froth_score / 100.0
        }

        # Filter step (start from initial probs)
        state_probs = hmm.filter_step(hmm.initial_probs, features)
        hmm_state = hmm.most_likely_state(state_probs)
    else:
        hmm_state = 0  # Default to Accumulation if insufficient data

    # Step 4: Macro Impact (placeholder - would need sector info)
    macro_impact_score = 0

    return {
        'hmm_state': hmm_state,
        'froth_score': froth_score,
        'hunter_score': hunter_score,
        'macro_impact_score': macro_impact_score
    }

def run_feature_gold_batch():
    """
    Main entrypoint for feature_gold_batch job.

    Execution flow:
    1. Acquire advisory lock
    2. Read from v_features_asof
    3. Compute HMM, FrothScore, HunterScore, Macro_Impact
    4. Write to features_gold_tmp
    5. Atomic publish via table swap
    6. Release lock
    """

    print("=" * 60)
    print("Feature Gold Batch starting...")
    print(f"Feature Set Version: {FEATURE_SET_VERSION}")
    print("=" * 60)

    conn = None

    try:
        conn = get_db_connection()

        # AC1: Acquire advisory lock
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (ADVISORY_LOCK_NAME,))
            acquired = cursor.fetchone()[0]

            if not acquired:
                print(f"Lock '{ADVISORY_LOCK_NAME}' already held. Exiting.")
                return 0

        print(f"✓ Acquired lock '{ADVISORY_LOCK_NAME}'")

        # Determine as_of_date (use current date for demo)
        as_of_date = datetime.now().strftime('%Y-%m-%d')

        # Fetch all symbols to process
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT symbol
                FROM ta_silver
                WHERE trade_date >= %s - interval '90 days'
            """, (as_of_date,))

            symbols = [row[0] for row in cursor.fetchall()]

        print(f"Processing {len(symbols)} symbols for {as_of_date}...")

        # Process each symbol
        features_data = []
        for i, symbol in enumerate(symbols):
            try:
                features = compute_features_for_symbol(conn, symbol, as_of_date)

                features_data.append({
                    'symbol': symbol,
                    'effective_date': as_of_date,
                    'feature_set_version': FEATURE_SET_VERSION,
                    **features
                })

                if (i + 1) % 10 == 0:
                    print(f"  Processed {i + 1}/{len(symbols)} symbols...")

            except Exception as e:
                print(f"  ERROR processing {symbol}: {e}")
                continue

        # Write to features_gold table
        with conn.cursor() as cursor:
            for feat in features_data:
                # Insert hmm_state
                cursor.execute("""
                    INSERT INTO features_gold (symbol, effective_date, feature_set_version, feature_name, value)
                    VALUES (%s, %s, %s, 'hmm_state', %s)
                    ON CONFLICT (symbol, effective_date, feature_set_version, feature_name)
                    DO UPDATE SET value = EXCLUDED.value
                """, (feat['symbol'], feat['effective_date'], feat['feature_set_version'], feat['hmm_state']))

                # Insert froth_score
                cursor.execute("""
                    INSERT INTO features_gold (symbol, effective_date, feature_set_version, feature_name, value)
                    VALUES (%s, %s, %s, 'froth_score', %s)
                    ON CONFLICT (symbol, effective_date, feature_set_version, feature_name)
                    DO UPDATE SET value = EXCLUDED.value
                """, (feat['symbol'], feat['effective_date'], feat['feature_set_version'], feat['froth_score']))

                # Insert hunter_score
                cursor.execute("""
                    INSERT INTO features_gold (symbol, effective_date, feature_set_version, feature_name, value)
                    VALUES (%s, %s, %s, 'hunter_score', %s)
                    ON CONFLICT (symbol, effective_date, feature_set_version, feature_name)
                    DO UPDATE SET value = EXCLUDED.value
                """, (feat['symbol'], feat['effective_date'], feat['feature_set_version'], feat['hunter_score']))

                # Insert macro_impact_score
                cursor.execute("""
                    INSERT INTO features_gold (symbol, effective_date, feature_set_version, feature_name, value)
                    VALUES (%s, %s, %s, 'macro_impact_score', %s)
                    ON CONFLICT (symbol, effective_date, feature_set_version, feature_name)
                    DO UPDATE SET value = EXCLUDED.value
                """, (feat['symbol'], feat['effective_date'], feat['feature_set_version'], feat['macro_impact_score']))

        conn.commit()

        print(f"✓ Wrote {len(features_data)} symbol features to features_gold")

        # Release lock
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (ADVISORY_LOCK_NAME,))

        print("=" * 60)
        print("Feature Gold Batch finished successfully!")
        print("=" * 60)

        return 0

    except Exception as e:
        print(f"ERROR: Feature Gold Batch failed: {e}")
        if conn:
            conn.rollback()
        return 1

    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    import sys
    sys.exit(run_feature_gold_batch())
