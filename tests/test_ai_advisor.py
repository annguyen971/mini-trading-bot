
import os
import sys
from unittest.mock import MagicMock, patch
# import pytest
from fastapi.testclient import TestClient

# Add src to path
sys.path.append("/root/mini-trading-bot/mini-trading-bot/src/api")
sys.path.append("/root/mini-trading-bot/mini-trading-bot/src/core_lib")

# Mock environment variables before importing main
os.environ["ADMIN_KEY"] = "test_key"
os.environ["GOOGLE_API_KEY"] = "test_google_key"

# Mock database connection
sys.modules["core_lib.db"] = MagicMock()
sys.modules["core_lib.db"].get_db_connection = MagicMock()

# Import app
from api.main import app

client = TestClient(app)

@patch("api.main.get_db_connection")
@patch("api.main.genai.GenerativeModel")
def test_analyze_ai_endpoint(mock_genai_model, mock_get_db):
    # Setup Mock DB
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_get_db.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    
    # Mock Features
    mock_cursor.fetchall.side_effect = [
        # Features
        [
            {"effective_date": "2023-10-27", "HunterScore": 80, "FrothScore": 30, "hmm_state": 0},
            {"effective_date": "2023-10-26", "HunterScore": 75, "FrothScore": 25, "hmm_state": 0},
        ],
        # News
        [
            {"url_canonical": "url1", "sentiment_score": 0.8, "hype_raw": 0.5, "title": "Good News"},
        ]
    ]

    # Setup Mock Gemini
    mock_model_instance = MagicMock()
    mock_genai_model.return_value = mock_model_instance
    mock_response = MagicMock()
    mock_response.text = '```json\n{"markdown_analysis": "Buy this stock."}\n```'
    mock_model_instance.generate_content.return_value = mock_response

    # Call Endpoint
    response = client.post(
        "/analyze/ai", 
        json={"symbol": "TEST"},
        headers={"X-ADMIN-KEY": "test_key"}
    )

    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "TEST"
    assert data["analysis"] == "Buy this stock."
    assert data["model"] == "gemini-2.0-flash"
    
    # Verify Gemini was called with correct prompt context
    args, _ = mock_model_instance.generate_content.call_args
    prompt = args[0]
    assert "HunterScore: 80" in prompt
    assert "Good News" in prompt

if __name__ == "__main__":
    # Manually run the test function if pytest is not available or for quick check
    try:
        test_analyze_ai_endpoint()
        print("Test Passed!")
    except Exception as e:
        print(f"Test Failed: {e}")
        import traceback
        traceback.print_exc()
