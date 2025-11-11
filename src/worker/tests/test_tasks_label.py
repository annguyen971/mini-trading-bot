# src/worker/tests/test_tasks_label.py
import pytest
import polars as pl
from polars.testing import assert_frame_equal

# Import the functions to be tested
from worker.tasks_label import run_snorkel_labeling, run_active_learning_sampling

# --- Fixtures ---

@pytest.fixture
def mock_features_asof():
    """Provides a mock DataFrame for v_features_asof."""
    data = {
        "symbol": ["AAA", "BBB", "CCC", "DDD", "EEE"],
        "effective_date": ["2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"],
        "rsi_14": [25, 50, 75, 20, 80],
        "hmm_state_top1": [0, 1, 2, 0, 3],
        "hmm_state_duration": [12, 5, 2, 15, 1],
        "macro_impact_score": [1, 2, 4, -1, -3],
        "macro_shock_flag": [0, 0, 0, 0, 1],
        "HunterScore": [70, 85, 50, 90, 40],
        "FrothScore": [30, 15, 60, 10, 70],
        "sector": ["Tech", "Finance", "Tech", "Finance", "Consumer"],
        "cap_tercile": [1, 2, 1, 3, 2],
    }
    return pl.DataFrame(data).with_columns(
        pl.col("effective_date").str.to_date()
    )

# --- Tests for run_snorkel_labeling ---

def test_run_snorkel_labeling_output_schema(mock_features_asof):
    """Tests if the output DataFrame has the correct schema."""
    df_silver = run_snorkel_labeling(mock_features_asof)

    expected_cols = [
        "symbol", "effective_date", "state_snorkel", "probability",
        "entropy", "is_auto_accepted", "label_model_version", "lf_fingerprint"
    ]

    for col in expected_cols:
        assert col in df_silver.columns

    assert df_silver.height == mock_features_asof.height

# --- Tests for run_active_learning_sampling ---

def test_run_active_learning_sampling_placeholder():
    """Placeholder test for the AL sampling logic."""
    # This will be expanded with more specific tests.
    assert True

import datetime

# --- As-of Attack Test ---

@pytest.fixture
def mock_features_asof_future_data(mock_features_asof):
    """Creates a mock DataFrame with a future date."""
    future_date = datetime.date.today() + datetime.timedelta(days=1)

    df_contaminated = mock_features_asof.with_columns(
        pl.when(pl.col("symbol") == "EEE")
        .then(future_date)
        .otherwise(pl.col("effective_date"))
        .alias("effective_date")
    )
    return df_contaminated


def test_as_of_attack_failure(mock_features_asof_future_data):
    """
    (Story 3.1/AC7)
    Tests that the pipeline fails with a ValueError when future data is detected.
    """
    with pytest.raises(ValueError, match="Future data detected"):
        run_snorkel_labeling(mock_features_asof_future_data)
