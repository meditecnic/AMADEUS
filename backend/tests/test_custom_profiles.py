"""Q41 Slice B: multi OpenAI-compatible profiles + v9→v10 migration."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

import pytest

from app import db as db_module
from app.services.credentials import InMemoryCredentialStore
from app.services.provider_runtime import (
    configure_custom_provider,
    ensure_default_custom_singleton,
    provider_registry,
    restore_custom_provider_configuration,
    unregister_custom_provider,
)


def test_v9_to_v10_adds_profile_columns_atomically(tmp_path):
    control = tmp_path / "control.db"
    connection = sqlite3.connect(control)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta(key,value) VALUES('schema_version','9');
        CREATE TABLE provider_configurations (
            provider_id TEXT PRIMARY KEY,
            base_url TEXT NOT NULL,
            default_model TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
        INSERT INTO provider_configurations
            (provider_id, base_url, default_model, updated_at_utc)
        VALUES
            ('custom', 'http://127.0.0.1:1234/v1', 'local-model', '2026-07-01T00:00:00Z');
        """
    )
    connection.commit()
    connection.close()

    connection = sqlite3.connect(control)
    try:
        db_module._migrate_v9_to_v10(connection, "control")
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == "10"
        row = connection.execute(
            """SELECT display_name, enabled, created_at_utc, base_url
               FROM provider_configurations WHERE provider_id='custom'"""
        ).fetchone()
        assert row[0] == "OpenAI-compatible"
        assert int(row[1]) == 1
        assert row[2] == "2026-07-01T00:00:00Z"
        assert row[3] == "http://127.0.0.1:1234/v1"
    finally:
        connection.close()


def test_v9_to_v10_rolls_back_columns_and_version_on_mid_migration_failure(tmp_path, monkeypatch):
    control = tmp_path / "control.db"
    connection = sqlite3.connect(control)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta(key,value) VALUES('schema_version','9');
        CREATE TABLE provider_configurations (
            provider_id TEXT PRIMARY KEY,
            base_url TEXT NOT NULL,
            default_model TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
        INSERT INTO provider_configurations
            (provider_id, base_url, default_model, updated_at_utc)
        VALUES
            ('custom', 'http://127.0.0.1:1234/v1', 'local-model', '2026-07-01T00:00:00Z');
        """
    )
    connection.commit()
    connection.close()

    original_column_exists = db_module._column_exists

    def flaky_column_exists(conn, table, column):
        # After display_name lands, fail before enabled is added so the
        # transaction must roll back both columns and the version bump.
        if (
            column == "enabled"
            and original_column_exists(conn, table, "display_name")
        ):
            raise sqlite3.OperationalError("injected migration failure")
        return original_column_exists(conn, table, column)

    monkeypatch.setattr(db_module, "_column_exists", flaky_column_exists)
    connection = sqlite3.connect(control)
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected migration failure"):
            db_module._migrate_v9_to_v10(connection, "control")
    finally:
        connection.close()
        monkeypatch.undo()

    connection = sqlite3.connect(control)
    try:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == "9"
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(provider_configurations)")
        }
        assert "enabled" not in columns
        assert "display_name" not in columns
        assert "created_at_utc" not in columns
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_validate_schema_fails_when_provider_configurations_missing_v10_columns(
    app_client,
    monkeypatch,
):
    async def broken_control():
        class _Fake:
            async def execute(self, sql, *args, **kwargs):
                class _Cur:
                    def __init__(self, rows):
                        self._rows = rows

                    async def fetchone(self):
                        return self._rows[0] if self._rows else None

                    async def fetchall(self):
                        return self._rows

                text = str(sql)
                if "schema_version" in text:
                    return _Cur([("10",)])
                if "PRAGMA table_info(shared_user_facts)" in text:
                    return _Cur([(0, col) for col in db_module.SHARED_USER_FACTS_REQUIRED_COLUMNS])
                if "sqlite_master" in text and "shared_user_facts" in text:
                    return _Cur([(name,) for name in db_module.SHARED_USER_FACTS_REQUIRED_INDEXES])
                if "sqlite_master" in text and "provider_configurations" in text:
                    return _Cur([("provider_configurations",)])
                if "PRAGMA table_info(provider_configurations)" in text:
                    # Missing enabled/created_at_utc/display_name
                    return _Cur([
                        (0, "provider_id"),
                        (1, "base_url"),
                        (2, "default_model"),
                        (3, "updated_at_utc"),
                    ])
                return _Cur([])

            async def close(self):
                return None

        return _Fake()

    original_get_db = db_module.get_db

    async def fake_get_db(worldline, kind):
        if kind == "control":
            return await broken_control()
        return await original_get_db(worldline, kind)

    monkeypatch.setattr(db_module, "get_db", fake_get_db)
    result = await db_module.validate_schema()
    assert result["ok"] is False
    assert result["databases"]["control"] is False


@pytest.mark.asyncio
async def test_create_patch_and_soft_disable_custom_profiles(app_client, monkeypatch):
    store = InMemoryCredentialStore()
    monkeypatch.setattr("app.routers.providers.credential_store", store)
    provider_id = None
    try:
        created = app_client.post(
            "/api/providers/custom-profiles",
            json={
                "display_name": "Local LM Studio",
                "base_url": "http://127.0.0.1:1234/v1",
                "default_model": "qwen-local",
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        provider_id = body["provider_id"]
        assert provider_id.startswith("custom:")
        assert body["display_name"] == "Local LM Studio"
        assert "api_key" not in body
        assert body["enabled"] is True

        listed = app_client.get("/api/providers")
        assert listed.status_code == 200
        by_id = {row["id"]: row for row in listed.json()}
        assert by_id[provider_id]["source"] == "user_config"
        assert by_id[provider_id]["enabled"] is True
        assert by_id[provider_id]["default_model"] == "qwen-local"
        assert "sk-" not in listed.text

        patched = app_client.patch(
            f"/api/providers/custom-profiles/{provider_id}",
            json={"default_model": "llama-local", "display_name": "Local Llama"},
        )
        assert patched.status_code == 200
        assert patched.json()["default_model"] == "llama-local"
        assert patched.json()["display_name"] == "Local Llama"

        store.set(provider_id, "sk-secret-should-not-echo")
        cred = app_client.post(
            f"/api/providers/{provider_id}/credentials",
            json={"api_key": "sk-secret-should-not-echo"},
        )
        assert cred.status_code == 200
        assert cred.json() == {"provider_id": provider_id, "configured": True}
        assert "sk-secret" not in cred.text

        deleted = app_client.delete(f"/api/providers/custom-profiles/{provider_id}")
        assert deleted.status_code == 200
        assert deleted.json()["enabled"] is False
        assert store.get(provider_id) is None

        listed_after = app_client.get("/api/providers")
        by_id_after = {row["id"]: row for row in listed_after.json()}
        assert by_id_after[provider_id]["enabled"] is False
        assert by_id_after[provider_id]["configured"] is False
        assert by_id_after[provider_id]["default_model_capability"]["callable"] is False

        with pytest.raises(KeyError):
            provider_registry.snapshot(provider_id)
        assert all(row.id != provider_id for row in provider_registry.list_definitions())
    finally:
        if provider_id:
            app_client.delete(f"/api/providers/custom-profiles/{provider_id}")
            provider_registry.unregister(provider_id)


@pytest.mark.asyncio
async def test_legacy_custom_delete_stays_unregistered_and_survives_restore(
    app_client,
    monkeypatch,
):
    store = InMemoryCredentialStore()
    monkeypatch.setattr("app.routers.providers.credential_store", store)

    # Ensure singleton exists, then soft-disable.
    ensure_default_custom_singleton()
    deleted = app_client.delete("/api/providers/custom-profiles/custom")
    assert deleted.status_code == 200
    assert deleted.json()["enabled"] is False
    with pytest.raises(KeyError):
        provider_registry.snapshot("custom")

    # Simulated process restart restore: disabled row must stay unavailable.
    ensure_default_custom_singleton()  # would re-add if restore logic is wrong
    await restore_custom_provider_configuration()
    with pytest.raises(KeyError):
        provider_registry.snapshot("custom")
    listed = app_client.get("/api/providers").json()
    custom_row = next(row for row in listed if row["id"] == "custom")
    assert custom_row["enabled"] is False
    assert custom_row["default_model_capability"]["callable"] is False


@pytest.mark.asyncio
async def test_delete_persists_disable_before_credential_failure_and_is_retryable(
    app_client,
    monkeypatch,
):
    store = InMemoryCredentialStore()
    monkeypatch.setattr("app.routers.providers.credential_store", store)
    provider_id = app_client.post(
        "/api/providers/custom-profiles",
        json={
            "display_name": "Retry Profile",
            "base_url": "http://127.0.0.1:5555/v1",
            "default_model": "retry-model",
        },
    ).json()["provider_id"]
    store.set(provider_id, "sk-retry-secret")

    calls = {"n": 0}
    original_delete = store.delete

    def flaky_delete(pid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("credential backend unavailable")
        return original_delete(pid)

    monkeypatch.setattr(store, "delete", flaky_delete)

    first = app_client.delete(f"/api/providers/custom-profiles/{provider_id}")
    assert first.status_code == 200
    assert first.json()["enabled"] is False
    # Profile must not be callable even if credential wipe failed.
    with pytest.raises(KeyError):
        provider_registry.snapshot(provider_id)
    assert store.get(provider_id) == "sk-retry-secret"
    assert first.json()["configured"] is True

    second = app_client.delete(f"/api/providers/custom-profiles/{provider_id}")
    assert second.status_code == 200
    assert second.json()["enabled"] is False
    assert second.json()["configured"] is False
    assert store.get(provider_id) is None
    provider_registry.unregister(provider_id)


@pytest.mark.asyncio
async def test_delete_returns_error_when_db_disable_fails(app_client, monkeypatch):
    provider_id = app_client.post(
        "/api/providers/custom-profiles",
        json={
            "display_name": "DB Fail",
            "base_url": "http://127.0.0.1:5556/v1",
            "default_model": "db-fail",
        },
    ).json()["provider_id"]

    monkeypatch.setattr(
        "app.routers.providers.soft_disable_provider_configuration",
        AsyncMock(side_effect=RuntimeError("db locked")),
    )
    try:
        response = app_client.delete(f"/api/providers/custom-profiles/{provider_id}")
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "provider_disable_failed"
        # Must still be callable if DB disable failed (no dangerous half state where
        # API claims disabled while registry remains).
        assert provider_registry.snapshot(provider_id, allow_unavailable=True).model_id == "db-fail"
    finally:
        monkeypatch.undo()
        app_client.delete(f"/api/providers/custom-profiles/{provider_id}")
        provider_registry.unregister(provider_id)


@pytest.mark.asyncio
async def test_save_and_soft_disable_return_committed_values_without_reread(
    app_client,
):
    """Commit success is declared from known fields."""
    from app import db as db_mod

    pid = "custom:11111111-1111-1111-1111-111111111111"
    saved = await db_mod.save_provider_configuration(
        pid,
        base_url="http://127.0.0.1:1/v1",
        default_model="m",
        display_name="Known",
        enabled=True,
    )
    assert saved["provider_id"] == pid
    assert saved["enabled"] is True
    assert saved["display_name"] == "Known"
    assert saved["base_url"] == "http://127.0.0.1:1/v1"
    disabled = await db_mod.soft_disable_provider_configuration(pid)
    assert disabled["enabled"] is False
    assert disabled["display_name"] == "Known"


@pytest.mark.asyncio
async def test_create_rolls_back_when_register_fails(app_client, monkeypatch):
    from app.routers import providers as providers_router
    from app.db import get_provider_configuration, list_provider_configurations

    def boom(**kwargs):
        raise RuntimeError("adapter boom")

    monkeypatch.setattr(providers_router, "configure_custom_provider", boom)
    response = app_client.post(
        "/api/providers/custom-profiles",
        json={
            "display_name": "Ghost",
            "base_url": "http://127.0.0.1:5557/v1",
            "default_model": "ghost-model",
        },
    )
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "provider_register_failed"
    # No ghost runtime registration.
    assert all(
        not (row.id.startswith("custom:") and row.default_model == "ghost-model")
        for row in provider_registry.list_definitions()
    )
    # Durable row must be disabled so restore cannot revive it.
    rows = await list_provider_configurations(include_disabled=True)
    ghosts = [r for r in rows if r.get("default_model") == "ghost-model"]
    assert ghosts
    assert all(r.get("enabled") is False for r in ghosts)
    await restore_custom_provider_configuration()
    assert all(
        not (row.id.startswith("custom:") and row.default_model == "ghost-model")
        for row in provider_registry.list_definitions()
    )
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_patch_and_legacy_patch_register_failure_fail_closed(app_client, monkeypatch):
    from app.routers import providers as providers_router
    from app.db import get_provider_configuration

    ensure_default_custom_singleton()
    provider_id = app_client.post(
        "/api/providers/custom-profiles",
        json={
            "display_name": "Patch Target",
            "base_url": "http://127.0.0.1:5558/v1",
            "default_model": "patch-ok",
        },
    ).json()["provider_id"]

    def boom(**kwargs):
        raise RuntimeError("register failed")

    monkeypatch.setattr(providers_router, "configure_custom_provider", boom)

    patch_resp = app_client.patch(
        f"/api/providers/custom-profiles/{provider_id}",
        json={"default_model": "patch-bad"},
    )
    assert patch_resp.status_code == 500
    row = await get_provider_configuration(provider_id)
    assert row is not None
    assert row["enabled"] is False
    with pytest.raises(KeyError):
        provider_registry.snapshot(provider_id)

    legacy = app_client.patch(
        "/api/providers/custom/configuration",
        json={
            "base_url": "http://127.0.0.1:5559/v1",
            "default_model": "legacy-bad",
            "display_name": "Legacy Bad",
        },
    )
    assert legacy.status_code == 500
    custom_row = await get_provider_configuration("custom")
    assert custom_row is not None
    assert custom_row["enabled"] is False
    with pytest.raises(KeyError):
        provider_registry.snapshot("custom")

    monkeypatch.undo()
    configure_custom_provider(
        provider_id="custom",
        base_url="http://127.0.0.1:1234/v1",
        default_model="local-model",
        display_name="OpenAI-compatible",
    )
    provider_registry.unregister(provider_id)


@pytest.mark.asyncio
async def test_clear_runtime_unregisters_even_if_cache_clear_raises(app_client, monkeypatch):
    from app.routers import providers as providers_router

    provider_id = app_client.post(
        "/api/providers/custom-profiles",
        json={
            "display_name": "Cache Fail",
            "base_url": "http://127.0.0.1:5560/v1",
            "default_model": "cache-fail",
        },
    ).json()["provider_id"]
    assert any(d.id == provider_id for d in provider_registry.list_definitions())

    monkeypatch.setattr(
        providers_router.provider_model_cache,
        "clear",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache clear boom")),
    )
    providers_router._clear_runtime_surfaces(provider_id)
    with pytest.raises(KeyError):
        provider_registry.snapshot(provider_id)
    monkeypatch.undo()
    app_client.delete(f"/api/providers/custom-profiles/{provider_id}")


@pytest.mark.asyncio
async def test_legacy_custom_configuration_still_works(app_client, monkeypatch):
    from app.services.provider_runtime import custom_provider_settings

    original = custom_provider_settings() if any(
        d.id == "custom" for d in provider_registry.list_definitions()
    ) else {
        "base_url": "http://127.0.0.1:1234/v1",
        "default_model": "local-model",
        "display_name": "OpenAI-compatible",
    }
    ensure_default_custom_singleton()
    try:
        response = app_client.patch(
            "/api/providers/custom/configuration",
            json={
                "base_url": "http://127.0.0.1:11434/v1/",
                "default_model": "ollama-model",
                "display_name": "Ollama",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["base_url"] == "http://127.0.0.1:11434/v1"
        assert payload["default_model"] == "ollama-model"
        assert payload["display_name"] == "Ollama"
        assert "api_key" not in payload
        snap = provider_registry.snapshot("custom")
        assert snap.adapter.base_url == "http://127.0.0.1:11434/v1"
        assert snap.model_id == "ollama-model"
    finally:
        configure_custom_provider(
            base_url=original["base_url"],
            default_model=original["default_model"],
            display_name=original.get("display_name"),
        )


@pytest.mark.asyncio
async def test_model_cache_is_isolated_per_custom_profile(app_client, monkeypatch):
    from app.routers import providers

    id_a = id_b = None
    try:
        created_a = app_client.post(
            "/api/providers/custom-profiles",
            json={
                "display_name": "Profile A",
                "base_url": "http://127.0.0.1:9001/v1",
                "default_model": "model-a",
            },
        ).json()
        created_b = app_client.post(
            "/api/providers/custom-profiles",
            json={
                "display_name": "Profile B",
                "base_url": "http://127.0.0.1:9002/v1",
                "default_model": "model-b",
            },
        ).json()
        id_a = created_a["provider_id"]
        id_b = created_b["provider_id"]

        async def list_a(*, api_key=None):
            return [{"id": "only-a"}]

        async def list_b(*, api_key=None):
            return [{"id": "only-b"}]

        monkeypatch.setattr(
            provider_registry.snapshot(id_a, allow_unavailable=True).adapter,
            "list_models",
            list_a,
        )
        monkeypatch.setattr(
            provider_registry.snapshot(id_b, allow_unavailable=True).adapter,
            "list_models",
            list_b,
        )
        providers.provider_model_cache.clear()

        resp_a = app_client.get(f"/api/providers/{id_a}/models?refresh=true")
        resp_b = app_client.get(f"/api/providers/{id_b}/models?refresh=true")
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        assert resp_a.json()["models"] == ["only-a"]
        assert resp_b.json()["models"] == ["only-b"]
        assert app_client.get(f"/api/providers/{id_a}/models").json()["models"] == ["only-a"]
        assert app_client.get(f"/api/providers/{id_b}/models").json()["models"] == ["only-b"]
    finally:
        for provider_id in (id_a, id_b):
            if provider_id:
                app_client.delete(f"/api/providers/custom-profiles/{provider_id}")
                provider_registry.unregister(provider_id)
