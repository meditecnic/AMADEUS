import pytest

from app.services.memory import memory_service
from app.services.memory_v11.contracts import MemoryValidationError
from test_memory_v11_api import (
    isolated_client, _seed_observation, _db_rows, _reconciler_apply, _s3d2_obs,
    _ApiDeterministicEmbedder,
)

SCOPE = {"session_id": "confirm", "worldline": "steins_gate", "identity_mode": "self"}


def confirm(client, oid, **scope):
    return client.post(f"/api/memory/observations/{oid}/confirm", params=SCOPE | scope)


def candidate():
    # A real candidate and source link, via the existing extraction write path.
    _reconciler_apply(session_id="confirm", observations=[_s3d2_obs(
        ref="o1", display_text="喜欢 Dr.pepper", source_message_id=9001, confidence=0.4,
    )], operations=[{"op": "CREATE", "observation_ref": "o1", "fact_text": "喜欢 Dr.pepper"}], source_message_id=9001)
    return _db_rows("SELECT observation_id FROM memory_observations WHERE session_id='confirm'")[0]["observation_id"]


def test_confirmation_returns_ledger_card_and_keeps_provenance(isolated_client):
    oid = candidate()
    response = confirm(isolated_client, oid)
    assert response.status_code == 200, response.text
    fact = response.json()
    assert fact["display_text"] == "喜欢 Dr.pepper"
    assert fact["confidence"] == 1.0 and fact["source"] == "user_confirm"
    listed = isolated_client.get("/api/memory/facts", params=SCOPE).json()["facts"]
    assert listed == [fact]
    assert _db_rows("SELECT status FROM memory_observations WHERE observation_id=?", (oid,))[0]["status"] == "attached"
    assert len(_db_rows("SELECT * FROM stable_fact_version_embeddings WHERE fact_id=?", (fact["fact_id"],))) == 1
    details = isolated_client.get(f"/api/memory/facts/{fact['fact_id']}/details", params=SCOPE).json()
    assert details["provenance"][0]["observation_id"] == oid
    assert details["provenance"][0]["source_message_id"] == 9001
    assert confirm(isolated_client, oid).status_code == 409


@pytest.mark.parametrize("status", ["ignored", "attached", "rejected", "expired", "deleted"])
def test_only_candidate_can_be_confirmed(isolated_client, status):
    oid = _seed_observation(session_id="confirm", display_text="test", status=status)
    response = confirm(isolated_client, oid)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "observation_not_confirmable"
    assert not _db_rows("SELECT * FROM stable_facts WHERE session_id='confirm'")


@pytest.mark.parametrize("scope", [{"session_id": "other"}, {"worldline": "beta"}, {"identity_mode": "okabe"}])
def test_confirmation_scope_isolation(isolated_client, scope):
    oid = candidate()
    assert confirm(isolated_client, oid, **scope).status_code == 404


def test_embedding_unavailable_leaves_candidate_retryable(isolated_client, monkeypatch):
    oid = candidate()
    monkeypatch.setattr(memory_service, "embedder", _ApiDeterministicEmbedder(fail_always=True))
    response = confirm(isolated_client, oid)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "embedding_not_ready"
    assert _db_rows("SELECT status FROM memory_observations WHERE observation_id=?", (oid,))[0]["status"] == "candidate"
    assert not _db_rows("SELECT * FROM stable_facts WHERE session_id='confirm'")


@pytest.mark.parametrize("operation", ["REFINE", "SUPERSEDE"])
def test_automatic_extraction_cannot_overwrite_user_confirmed_fact(isolated_client, operation):
    oid = candidate()
    fact = confirm(isolated_client, oid).json()
    with pytest.raises(MemoryValidationError) as error:
        _reconciler_apply(session_id="confirm", observations=[_s3d2_obs(
        ref="o2", display_text="不喜欢 Dr.pepper", source_message_id=9002,
    )], operations=[{"op": operation, "observation_ref": "o2", "target_fact_id": fact["fact_id"],
                    "expected_version": 1, "fact_text": "不喜欢 Dr.pepper"}], source_message_id=9002,
        candidate_allowlist=(fact["fact_id"],))
    assert error.value.code == "user_fact_protected"
    row = isolated_client.get("/api/memory/facts", params=SCOPE).json()["facts"][0]
    assert row["display_text"] == "喜欢 Dr.pepper" and row["active_version"] == 1


@pytest.mark.parametrize("forget", [False, True])
def test_confirmed_fact_follows_existing_erasure_rules(isolated_client, forget):
    from app.services.conversation_erasure import request_erasure
    oid = candidate()
    fact = confirm(isolated_client, oid).json()
    async def erase():
        return await request_erasure(session_id="confirm", identity_mode="self", worldline="steins_gate",
            conversation_id="conv-s3d2-9001", action="delete", forget_long_term=forget)
    isolated_client.portal.call(erase)
    rows = isolated_client.get("/api/memory/facts", params=SCOPE).json()["facts"]
    if forget:
        assert rows == []
        assert not _db_rows("SELECT * FROM stable_fact_version_embeddings WHERE fact_id=?", (fact["fact_id"],))
    else:
        assert rows[0]["fact_id"] == fact["fact_id"]
        details = isolated_client.get(f"/api/memory/facts/{fact['fact_id']}/details", params=SCOPE).json()
        assert details["provenance"][0]["source_state"] == "deleted"
        assert details["provenance"][0]["excerpt"] is None


def test_confirm_rolls_back_fact_and_status_on_vector_insert_failure(isolated_client, monkeypatch):
    from app.services.memory_v11 import indexing
    from unittest.mock import AsyncMock
    oid = candidate()
    monkeypatch.setattr(indexing, "insert_version_embedding", AsyncMock(side_effect=RuntimeError("injected")))
    assert confirm(isolated_client, oid).status_code == 500
    assert not _db_rows("SELECT * FROM stable_facts WHERE session_id='confirm'")
    assert _db_rows("SELECT status FROM memory_observations WHERE observation_id=?", (oid,))[0]["status"] == "candidate"
