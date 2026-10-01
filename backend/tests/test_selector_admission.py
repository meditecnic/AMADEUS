"""准入持久化、配置版本冲突和旧 control 库升级。"""

import sqlite3

import pytest

from app import db
from app.services.provider_runtime import provider_registry, restore_custom_provider_configuration


@pytest.fixture(autouse=True)
def admission_data(monkeypatch, tmp_path):
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    db._initialized_paths.clear()
    yield
    for definition in list(provider_registry.list_definitions()):
        if definition.id.startswith("custom:"):
            provider_registry.unregister(definition.id)
    db._initialized_paths.clear()


def create_profile(client):
    response = client.post("/api/providers/custom-profiles", json={
        "display_name": "Admission test",
        "base_url": "http://127.0.0.1:1/v1",
        "default_model": "model-a",
    })
    assert response.status_code == 200
    return response.json()


def listed_profile(client, provider_id):
    return next(row for row in client.get("/api/providers").json() if row["id"] == provider_id)


def admit(client, profile, state):
    return client.patch(f"/api/providers/custom-profiles/{profile['provider_id']}", json={
        "selector_admission": state,
        "expected_configuration_version": profile["configuration_version"],
    })


@pytest.mark.parametrize("state", ["pending", "admitted", "manual"])
def test_admission_survives_runtime_restore_and_disable(app_client, state):
    created = create_profile(app_client)
    assert created["selector_admission"] == "pending"
    assert created["configuration_version"]
    provider_id = created["provider_id"]
    response = admit(app_client, created, state)
    assert response.status_code == 200
    assert response.json()["selector_admission"] == state
    provider_registry.unregister(provider_id)
    db._initialized_paths.clear()
    app_client.portal.call(db.init_db)
    app_client.portal.call(restore_custom_provider_configuration)
    assert listed_profile(app_client, provider_id)["selector_admission"] == state
    assert app_client.delete(f"/api/providers/custom-profiles/{provider_id}").status_code == 200
    disabled = listed_profile(app_client, provider_id)
    assert disabled["enabled"] is False
    assert disabled["selector_admission"] == state
    assert admit(app_client, created, "admitted").status_code == 404
    builtins = [row for row in app_client.get("/api/providers").json() if row["source"] == "builtin"]
    assert builtins and all(row["selector_admission"] == "admitted" for row in builtins)


@pytest.mark.parametrize("state", ["pending", "admitted", "manual"])
@pytest.mark.parametrize("change", ["base_url", "default_model", "credential", "delete_credential"])
def test_config_changes_keep_admission_and_reject_old_result(app_client, state, change):
    created = create_profile(app_client)
    original = admit(app_client, created, state).json()
    provider_id = created["provider_id"]
    if change in ("credential", "delete_credential"):
        if change == "credential":
            response = app_client.post(f"/api/providers/{provider_id}/credentials", json={"api_key": "test-key"})
        else:
            response = app_client.delete(f"/api/providers/{provider_id}/credentials")
    else:
        value = "http://127.0.0.1:2/v1" if change == "base_url" else "model-b"
        response = app_client.patch(f"/api/providers/custom-profiles/{provider_id}", json={change: value})
        assert response.json()["selector_admission"] == state
    assert response.status_code == 200
    current = listed_profile(app_client, provider_id)
    assert current["selector_admission"] == state
    assert current["configuration_version"] != original["configuration_version"]
    stale = admit(app_client, original, "admitted")
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "configuration_changed"
    assert listed_profile(app_client, provider_id)["selector_admission"] == state
    current["provider_id"] = provider_id
    assert admit(app_client, current, "admitted").status_code == 200


@pytest.mark.parametrize("body", [
    {"selector_admission": "admitted"},
    {"selector_admission": "checked", "expected_configuration_version": "old"},
    {"expected_configuration_version": "old"},
    {"selector_admission": "manual", "expected_configuration_version": "old", "default_model": "model-b"},
])
def test_invalid_admission_patch_has_no_configuration_side_effect(app_client, body):
    created = create_profile(app_client)
    provider_id = created["provider_id"]
    before = listed_profile(app_client, provider_id)
    response = app_client.patch(f"/api/providers/custom-profiles/{provider_id}", json=body)
    assert response.status_code == 422
    assert listed_profile(app_client, provider_id) == before


def test_existing_control_profiles_migrate_once_to_admitted(tmp_path):
    path = tmp_path / "control.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES('schema_version', '11');
            CREATE TABLE provider_configurations (
                provider_id TEXT PRIMARY KEY, base_url TEXT NOT NULL,
                default_model TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1, created_at_utc TEXT NOT NULL DEFAULT '',
                updated_at_utc TEXT NOT NULL);
            INSERT INTO provider_configurations VALUES('custom:old', 'http://127.0.0.1:1/v1', 'm', 'Old', 1, 'old', 'old');
            INSERT INTO provider_configurations VALUES('custom:disabled', 'http://127.0.0.1:1/v1', 'm', 'Disabled', 0, 'old', 'old');
        """)
    db._initialize_sync(str(path), db.CONTROL_SCHEMA, "control")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT selector_admission FROM provider_configurations").fetchall() == [("admitted",), ("admitted",)]
        connection.execute("UPDATE provider_configurations SET selector_admission='pending' WHERE provider_id='custom:old'")
    db._initialize_sync(str(path), db.CONTROL_SCHEMA, "control")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT selector_admission, enabled FROM provider_configurations WHERE provider_id='custom:old'").fetchone() == ("pending", 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE provider_configurations SET selector_admission='checked'")


def test_existing_admitted_profile_accepts_browser_state_migration(app_client):
    created = create_profile(app_client)
    assert admit(app_client, created, "admitted").status_code == 200
    assert admit(app_client, created, "pending").status_code == 200
    assert admit(app_client, created, "manual").status_code == 200
    assert listed_profile(app_client, created["provider_id"])["selector_admission"] == "manual"
