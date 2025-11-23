#!/usr/bin/env python3
"""Simple test to debug the /analyze/predator endpoint"""

from fastapi.testclient import TestClient
import sys
sys.path.insert(0, '/usr/local/lib/python3.11/site-packages')

from api.main import app

client = TestClient(app)

# Override admin key check
from api.main import verify_admin_key
app.dependency_overrides[verify_admin_key] = lambda: "test"

# Test with minimal data
response = client.post("/analyze/predator", json={"symbol": "VN30F1M"})

print(f"Status: {response.status_code}")
print(f"Response: {response.text[:500]}")

if response.status_code != 200:
    print("\nFull response:")
    print(response.text)
