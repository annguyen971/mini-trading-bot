import pytest
import pandas as pd
import numpy as np
from core_lib.predator_scenarios import (
    detect_scenario,
    calculate_pain_levels,
    SCENARIO_DISTRIBUTION_CLIMAX,
    SCENARIO_STEALTH_ACCUMULATION,
    SCENARIO_SHAKEOUT,
    SCENARIO_UPTHRUST,
    SCENARIO_NEUTRAL
)

def test_detect_distribution_climax():
    """AC1: Verify DISTRIBUTION_CLIMAX scenario detection"""
    features = {
        'hmm_state': 2,  # Euphoria
        'hype_crowd_z': 1.8,
        'hype_elitist_z': -0.7,
        'price_change': 0.05,
        'vol_rel': 1.2
    }
    assert detect_scenario(features) == SCENARIO_DISTRIBUTION_CLIMAX
    
    # Test with Breakout state (1)
    features['hmm_state'] = 1
    assert detect_scenario(features) == SCENARIO_DISTRIBUTION_CLIMAX

def test_detect_stealth_accumulation():
    """AC1: Verify STEALTH_ACCUMULATION scenario detection"""
    features = {
        'hmm_state': 0,  # Accumulation
        'hype_crowd_z': -1.6,
        'hype_elitist_z': 0.8,
        'price_change': -0.01,
        'vol_rel': 0.5
    }
    assert detect_scenario(features) == SCENARIO_STEALTH_ACCUMULATION

def test_detect_shakeout():
    """AC1: Verify SHAKEOUT scenario detection"""
    features = {
        'hmm_state': 0,
        'hype_crowd_z': 0.0,
        'hype_elitist_z': 0.5, # > 0
        'price_change': -0.04, # < -3%
        'vol_rel': 1.6 # > 1.5
    }
    assert detect_scenario(features) == SCENARIO_SHAKEOUT

def test_detect_upthrust():
    """AC1: Verify UPTHRUST scenario detection"""
    features = {
        'hmm_state': 2,
        'hype_crowd_z': 0.0,
        'hype_elitist_z': -0.5, # < 0
        'price_change': 0.04, # > 3%
        'vol_rel': 0.7 # < 0.8
    }
    assert detect_scenario(features) == SCENARIO_UPTHRUST

def test_detect_neutral():
    """AC1: Verify NEUTRAL scenario detection"""
    features = {
        'hmm_state': 0,
        'hype_crowd_z': 0.0,
        'hype_elitist_z': 0.0,
        'price_change': 0.0,
        'vol_rel': 1.0
    }
    assert detect_scenario(features) == SCENARIO_NEUTRAL

def test_calculate_pain_levels():
    """AC2: Verify liquidity map calculation"""
    # Create synthetic data: 
    # Price mostly around 50, with some outliers
    data = {
        'close': [50.0] * 10 + [40.0] * 2 + [60.0] * 2,
        'volume': [1000] * 10 + [100] * 2 + [100] * 2
    }
    df = pd.DataFrame(data)
    
    # The bin containing 50.0 should have max volume
    result = calculate_pain_levels(df, bins=10)
    
    # 50.0 should be close to the pain level
    assert 48.0 <= result['price'] <= 52.0
    assert result['vol_ratio'] > 0.8 # Most volume is at 50

def test_calculate_pain_levels_empty():
    """Verify handling of empty data"""
    df = pd.DataFrame(columns=['close', 'volume'])
    res = calculate_pain_levels(df)
    assert res['price'] == 0.0
    assert res['vol_ratio'] == 0.0

def test_calculate_pain_levels_sparse():
    """Verify handling of sparse data"""
    df = pd.DataFrame({'close': [10.0, 11.0], 'volume': [100, 100]})
    res = calculate_pain_levels(df)
    assert res['price'] == 10.5 # Mean
    assert res['vol_ratio'] == 0.0
