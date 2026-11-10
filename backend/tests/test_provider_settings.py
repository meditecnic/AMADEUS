import sqlite3

from app import db


def test_v13_settings_migration_preserves_configuration(tmp_path):
    path = tmp_path / "control.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(db.CONTROL_SCHEMA)
        connection.execute("INSERT INTO schema_meta VALUES('schema_version','13')")
        connection.execute("INSERT INTO provider_configurations(provider_id,base_url,default_model,updated_at_utc) VALUES('custom','https://example.invalid','model','old')")
    assert db._initialize_sync(str(path), db.CONTROL_SCHEMA, "control")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT default_model FROM provider_configurations").fetchone() == ("model",)
        assert connection.execute("SELECT * FROM provider_settings").fetchall() == []
        assert connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone() == (str(db.SCHEMA_VERSION),)


def test_builtin_and_custom_settings_survive_restart(app_client):
    for provider in ["deepseek", "custom"]:
        path = f"/api/providers/{provider}/settings"
        assert app_client.patch(path, json={"visible_models": ["a", "b"]}).status_code == 200
    db.reset_initialization_cache()
    app_client.portal.call(db.init_db)
    rows = app_client.get("/api/providers").json()
    for provider in ["deepseek", "custom"]:
        assert next(row for row in rows if row["id"] == provider)["visible_models"] == ["a", "b"]
    assert app_client.patch("/api/providers/missing/settings", json={"visible_models": []}).status_code == 404
