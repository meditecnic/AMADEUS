def test_auth_salieri_standard(app_client):
    """Test successful authentication with Gott würfelt nicht."""
    response = app_client.post("/api/auth/salieri", json={"password": "Gott würfelt nicht"})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}

def test_auth_salieri_ascii(app_client):
    """Test successful authentication with Gott wuerfelt nicht."""
    response = app_client.post("/api/auth/salieri", json={"password": "Gott wuerfelt nicht"})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}

def test_auth_salieri_simple(app_client):
    """Test successful authentication with gott wurfelt nicht."""
    response = app_client.post("/api/auth/salieri", json={"password": "gott wurfelt nicht"})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}

def test_auth_salieri_old_password_rejected(app_client):
    """Test K.331 is rejected under new credentials."""
    response = app_client.post("/api/auth/salieri", json={"password": "K.331"})
    assert response.status_code == 401
    assert "Invalid password" in response.json()["detail"]

def test_auth_salieri_wrong_password(app_client):
    """Test authentication failure with incorrect password."""
    response = app_client.post("/api/auth/salieri", json={"password": "wrong_pass"})
    assert response.status_code == 401
    assert "Invalid password" in response.json()["detail"]

def test_auth_salieri_missing_fields(app_client):
    """Test authentication rejection when password field is missing."""
    response = app_client.post("/api/auth/salieri", json={})
    assert response.status_code == 422
