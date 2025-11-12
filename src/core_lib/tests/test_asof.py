import pytest
import polars as pl
from polars.testing import assert_frame_equal
from unittest.mock import patch
from datetime import date, timedelta
import os

# Set environment variables for testing
os.environ['DB_URL'] = "mock_db_url"
os.environ['MACRO_TZ'] = "UTC"

# Since this test file is in core_lib, but tests a worker function,
# we need to ensure the worker module is importable.
# This is often handled by pytest's path configuration (e.g., in pyproject.toml),
# so we assume it works. If not, sys.path manipulation would be needed.
from worker.tasks_feature_gold import calculate_scores

def create_base_df():
    """Creates a base Polars DataFrame for testing."""
    return pl.DataFrame({
        "symbol": ["A", "B"],
        "sector": ["FINANCIALS", "IT"],
        "effective_date": [date(2023, 1, 1), date(2023, 1, 1)],
        "hype_crowd_z": [0.5, 1.0],
        "news_count_crowd_z": [0.2, 0.8],
        "S_tech_z": [0.6, 0.3],
        "breadth_contra_z": [0.4, 0.5],
        "hype_elitist_z": [1.2, 0.5],
        "H_ta_z": [0.8, 0.4],
        "H_cat_z": [0.5, 0.2],
        "catalyst_active": [1, 0]
    })

@patch('worker.tasks_feature_gold.read_sql_pl')
def test_ttl_override_is_active(mock_read_sql):
    """
    Tests that the UI override is used when its TTL is in the future.
    """
    base_df = create_base_df()

    # Mock return values for the database calls
    mock_impact_rt = pl.DataFrame({
        "sector": ["FINANCIALS", "IT"], "impact": [-0.5, -0.5], "confidence": [1.0, 1.0]
    })
    mock_ui_override = pl.DataFrame({
        "sector": ["FINANCIALS"], "weight": [0.9] # Active override for FINANCIALS
    })

    mock_read_sql.side_effect = [mock_impact_rt, mock_ui_override]

    # Run the function
    result_df = calculate_scores(base_df, conn=None, enable_blend=True)

    # Assert that the final impact for FINANCIALS is from the override (0.9)
    assert result_df.filter(pl.col('symbol') == 'A')['impact_final'].item() == 0.9
    # Assert that the final impact for IT is from the RT table (-0.5)
    assert result_df.filter(pl.col('symbol') == 'B')['impact_final'].item() == -0.5

@patch('worker.tasks_feature_gold.read_sql_pl')
def test_ttl_override_is_expired(mock_read_sql):
    """
    Tests that the UI override is IGNORED when its TTL has expired.
    The mock for read_sql_pl will return an empty DataFrame for the UI override,
    simulating that the WHERE clause on ttl_until filtered it out.
    """
    base_df = create_base_df()

    mock_impact_rt = pl.DataFrame({
        "sector": ["FINANCIALS", "IT"], "impact": [-0.5, 0.2], "confidence": [1.0, 1.0]
    })
    mock_ui_override_expired = pl.DataFrame({
        "sector": [], "weight": [] # Empty because it's expired
    })

    mock_read_sql.side_effect = [mock_impact_rt, mock_ui_override_expired]

    result_df = calculate_scores(base_df, conn=None, enable_blend=True)

    # Assert that the final impact for FINANCIALS is from the RT table (-0.5), not the expired override
    assert result_df.filter(pl.col('symbol') == 'A')['impact_final'].item() == -0.5
    # Assert that IT is also from the RT table
    assert result_df.filter(pl.col('symbol') == 'B')['impact_final'].item() == 0.2

@patch('worker.tasks_feature_gold.read_sql_pl')
def test_blend_logic_with_confidence_one_vs_zero(mock_read_sql):
    """
    Tests the blending logic with confidence=1 vs confidence=0.
    Based on the current implementation, 'confidence' is not used, so the result
    should be IDENTICAL. This test verifies the current behavior.
    """
    # --- Scenario 1: Confidence = 1.0 ---
    base_df = create_base_df()
    mock_impact_rt_conf1 = pl.DataFrame({
        "sector": ["FINANCIALS", "IT"], "impact": [0.8, -0.2], "confidence": [1.0, 1.0]
    })
    mock_ui_override_empty = pl.DataFrame({"sector": [], "weight": []})
    mock_read_sql.side_effect = [mock_impact_rt_conf1, mock_ui_override_empty]

    result_conf1 = calculate_scores(base_df, conn=None, enable_blend=True)

    # --- Scenario 2: Confidence = 0.0 ---
    base_df_2 = create_base_df()
    mock_impact_rt_conf0 = pl.DataFrame({
        "sector": ["FINANCIALS", "IT"], "impact": [0.8, -0.2], "confidence": [0.0, 0.0]
    })
    # Reset the mock's side_effect
    mock_read_sql.side_effect = [mock_impact_rt_conf0, mock_ui_override_empty]

    result_conf0 = calculate_scores(base_df_2, conn=None, enable_blend=True)

    # --- Assert ---
    # The final HunterScore should be exactly the same in both cases, as 'confidence' is not used in the calculation.
    assert_frame_equal(
        result_conf1.select("symbol", "HunterScore"),
        result_conf0.select("symbol", "HunterScore"),
        check_dtype=False
    )
    # Also check that the final impact is derived from 'impact', regardless of confidence
    assert result_conf1.filter(pl.col('symbol') == 'A')['impact_final'].item() == 0.8
    assert result_conf0.filter(pl.col('symbol') == 'A')['impact_final'].item() == 0.8
