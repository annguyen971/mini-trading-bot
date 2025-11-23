import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from api.main import app, verify_admin_key

# Mock Admin Key
app.dependency_overrides[verify_admin_key] = lambda: "test_key"

client = TestClient(app)

@pytest.fixture
def mock_db():
    with patch("api.main.get_db_connection") as mock:
        yield mock

@pytest.fixture
def mock_gemini():
    with patch("api.main.genai.GenerativeModel") as mock:
        yield mock

def test_analyze_predator_success(mock_db, mock_gemini):
    # Mock DB responses
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_db.return_value = mock_conn
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    
    # Setup fetchone and fetchall responses in order
    # The endpoint queries: latest_features, ta_rows, sa_rows, history_rows, macro_rows
    fetch_responses = [
        # latest_features (fetchone)
        {"hmm_state": 2, "HunterScore": 80, "FrothScore": 70, "effective_date": "2023-01-01"},
        # ta_rows (fetchall)
        [{"close": 100.0, "volume": 1000.0}, {"close": 95.0, "volume": 800.0}],
        # sa_rows (fetchall)
        [{"hype_raw": 0.5}],
        # history_rows (fetchall)
        [{"close": 100.0, "volume": 1000.0}] * 60,  # Need 60 for calculate_pain_levels
        # macro_rows (fetchall)
        [{"metric_name": "CPI", "value": 3.5}],
    ]
    
    fetch_index = [0]
    def side_effect_fetch(*args):
        result = fetch_responses[fetch_index[0]]
        fetch_index[0] += 1
        return result
    
    mock_cursor.fetchone.side_effect = lambda: side_effect_fetch()
    mock_cursor.fetchall.side_effect = lambda: side_effect_fetch()
    
    # Mock Gemini
    mock_model = MagicMock()
    mock_gemini.return_value = mock_model
    mock_response = MagicMock()
    mock_response.text = '```json\n{"predator_memo": "Test", "quant_explanation": "Test", "user_playbook": "Test"}\n```'
    mock_model.generate_content.return_value = mock_response
    
    response = client.post("/analyze/predator", json={"symbol": "TEST"})
    if response.status_code != 200:
        print(f"Error response: {response.status_code}")
        print(f"Response body: {response.text}")
    assert response.status_code == 200
    data = response.json()
    assert "scenario_tag" in data
    assert "ai_analysis" in data
    assert data["ai_analysis"]["predator_memo"] == "Test"

def test_simulate_success(mock_db):
    # Mock DB
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_db.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    
    mock_cursor.fetchone.return_value = {"hmm_state": 0, "HunterScore": 50, "FrothScore": 50}
    
    overrides = {
        "vol_rel": 2.0,
        "price_change": -0.05,
        "hype_crowd_z": -2.0,
        "hype_elitist_z": 1.0
    }
    
    response = client.post("/analyze/simulate", json={"symbol": "TEST", "overrides": overrides})
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_tag"] == "STEALTH_ACCUMULATION" # Based on rules: HMM 0, Crowd -2, Elite 1 -> Accumulation

def test_save_journal(mock_db):
    mock_conn = MagicMock()
    mock_db.return_value = mock_conn
    
    response = client.post("/predator/journal/save", json={"symbol": "TEST", "user_prediction": "I think it is accumulation"})
    assert response.status_code == 200
    assert response.json() == {"status": "saved"}
