import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_auth_salieri_success():
    """Test successful authentication with K.331."""
    response = client.post("/api/auth/salieri", json={"password": "K.331"})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}

def test_auth_salieri_wrong_password():
    """Test authentication failure with incorrect password."""
    response = client.post("/api/auth/salieri", json={"password": "wrong_pass"})
    assert response.status_code == 401
    assert "Invalid password" in response.json()["detail"]

def test_auth_salieri_missing_fields():
    """Test authentication rejection when password field is missing."""
    response = client.post("/api/auth/salieri", json={})
    assert response.status_code == 422
