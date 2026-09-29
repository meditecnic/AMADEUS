from __future__ import annotations

from dataclasses import replace
from uuid import uuid4


def _session(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def test_conversation_crud_search_pin_and_model_selection(app_client):
    session_id = _session("conversation-crud")
    created = app_client.post(
        "/api/conversations",
        json={
            "session_id": session_id,
            "worldline": "steins_gate",
            "title": "PhoneWave analysis",
            "provider_id": "openai",
            "model_id": "gpt-5.6-luna",
        },
    )

    assert created.status_code == 201
    conversation = created.json()
    assert conversation["title"] == "PhoneWave analysis"
    assert conversation["title_source"] == "manual"
    assert conversation["provider_id"] == "openai"
    assert conversation["identity_mode"] == "okabe"

    patched = app_client.patch(
        f"/api/conversations/{conversation['id']}",
        params={"session_id": session_id, "worldline": "steins_gate"},
        json={
            "title": "Pinned experiment",
            "is_pinned": True,
            "provider_id": "gemini",
            "model_id": "gemini-3.5-flash",
        },
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Pinned experiment"
    assert patched.json()["title_source"] == "manual"
    assert patched.json()["is_pinned"] is True
    assert patched.json()["provider_id"] == "gemini"
    assert patched.json()["identity_mode"] == "okabe"

    listed = app_client.get(
        "/api/conversations",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "query": "pinned",
        },
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [conversation["id"]]
    assert listed.json()[0]["identity_mode"] == "okabe"

    messages = app_client.get(
        f"/api/conversations/{conversation['id']}/messages",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert messages.status_code == 200
    assert messages.json() == []

    deleted = app_client.delete(
        f"/api/conversations/{conversation['id']}",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert deleted.status_code == 204
    missing = app_client.get(
        f"/api/conversations/{conversation['id']}",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert missing.status_code == 404


def test_conversation_uses_explicit_model_when_provider_default_is_unavailable(app_client, monkeypatch):
    from app.routers import conversations
    from app.services.model_catalog import model_catalog
    from app.services.provider_registry import ProviderRegistry

    definition = next(row for row in conversations.provider_registry.list_definitions() if row.id == "deepseek")
    adapter = conversations.provider_registry.snapshot("deepseek", "deepseek-flash").adapter
    registry = ProviderRegistry(model_catalog=model_catalog)
    registry.register(replace(definition, default_model="missing-default-model"), adapter)
    monkeypatch.setattr(conversations, "provider_registry", registry)

    session_id = _session("selected-model")
    created = app_client.post(
        "/api/conversations",
        json={"session_id": session_id, "provider_id": "deepseek", "model_id": "deepseek-flash"},
    )
    assert created.status_code == 201
    assert created.json()["model_id"] == "deepseek-flash"

    patched = app_client.patch(
        f"/api/conversations/{created.json()['id']}",
        params={"session_id": session_id},
        json={"model_id": "deepseek-flash"},
    )
    assert patched.status_code == 200
    assert patched.json()["model_id"] == "deepseek-flash"

    unavailable = app_client.post(
        "/api/conversations",
        json={"session_id": session_id, "provider_id": "deepseek", "model_id": "not-in-catalog"},
    )
    assert unavailable.status_code == 422
    assert unavailable.json()["detail"]["code"] == "model_unavailable"


def test_conversation_identity_mode_default_explicit_and_immutable(app_client):
    session_id = _session("identity-mode")
    defaulted = app_client.post(
        "/api/conversations",
        json={"session_id": session_id, "worldline": "steins_gate", "title": "Default mode"},
    )
    assert defaulted.status_code == 201
    assert defaulted.json()["identity_mode"] == "okabe"

    self_mode = app_client.post(
        "/api/conversations",
        json={
            "session_id": session_id,
            "worldline": "steins_gate",
            "title": "Self mode",
            "identity_mode": "self",
        },
    )
    assert self_mode.status_code == 201
    assert self_mode.json()["identity_mode"] == "self"

    illegal = app_client.post(
        "/api/conversations",
        json={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "admin",
        },
    )
    assert illegal.status_code == 422
    assert illegal.json()["detail"]["code"] == "invalid_identity_mode"

    cid = defaulted.json()["id"]
    patched = app_client.patch(
        f"/api/conversations/{cid}",
        params={"session_id": session_id, "worldline": "steins_gate"},
        json={"identity_mode": "self"},
    )
    assert patched.status_code == 422
    assert patched.json()["detail"]["code"] == "identity_mode_immutable"

    fetched = app_client.get(
        f"/api/conversations/{cid}",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["identity_mode"] == "okabe"

    listed = app_client.get(
        "/api/conversations",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert listed.status_code == 200
    modes = {row["id"]: row["identity_mode"] for row in listed.json()}
    assert modes[cid] == "okabe"
    assert modes[self_mode.json()["id"]] == "self"


def test_conversation_api_enforces_owner_worldline_and_provider(app_client):
    owner = _session("owner")
    created = app_client.post(
        "/api/conversations",
        json={"session_id": owner, "worldline": "beta", "title": "Beta only"},
    ).json()

    wrong_owner = app_client.get(
        f"/api/conversations/{created['id']}",
        params={"session_id": _session("intruder"), "worldline": "beta"},
    )
    wrong_worldline = app_client.get(
        f"/api/conversations/{created['id']}",
        params={"session_id": owner, "worldline": "steins_gate"},
    )
    unknown_provider = app_client.post(
        "/api/conversations",
        json={
            "session_id": owner,
            "worldline": "beta",
            "provider_id": "missing-provider",
            "model_id": "anything",
        },
    )

    assert wrong_owner.status_code == 404
    assert wrong_worldline.status_code == 404
    assert unknown_provider.status_code == 422


def test_deleting_last_default_conversation_stays_empty_after_reconnect(app_client):
    session_id = _session("default-delete")
    listed = app_client.get(
        "/api/conversations",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert listed.status_code == 200
    default = next(row for row in listed.json() if row["is_default"])

    response = app_client.delete(
        f"/api/conversations/{default['id']}",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )

    assert response.status_code == 204
    assert app_client.get(
        "/api/conversations",
        params={"session_id": session_id, "worldline": "steins_gate"},
    ).json() == []

    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as socket:
        auth = {"type": "auth", "protocol_version": 2, "worldline": "steins_gate"}
        socket.send_json(auth)
        socket.send_json(auth)
        assert socket.receive_json()["type"] == "session.ready"
        assert "已完成认证" in socket.receive_json()["message"]

    assert app_client.get(
        "/api/conversations",
        params={"session_id": session_id, "worldline": "steins_gate"},
    ).json() == []


def test_conversation_forget_defaults_to_messages_only(app_client):
    session_id = _session("forget-api")
    conversation = app_client.post(
        "/api/conversations",
        json={"session_id": session_id, "title": "Forgettable"},
    ).json()

    response = app_client.post(
        f"/api/conversations/{conversation['id']}/forget",
        params={"session_id": session_id, "worldline": "steins_gate"},
        json={"forget_long_term": False},
    )

    assert response.status_code == 200
    assert response.json() == {
        "messages_deleted": 0,
        "summaries_deleted": 0,
        "episodic_deleted": 0,
        "core_facts_deleted": 0,
    }
