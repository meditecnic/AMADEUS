"""S4-MEMORY-SPINE projection: live HTTP + schema (contract §6)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.services.memory import EmbeddingAdapter


def _unit_vector(seed: str) -> list[float]:
    values: list[float] = []
    for block in range(12):
        digest = hashlib.sha256(f"{seed}:{block}".encode("utf-8")).digest()
        values.extend(int(byte) - 127.5 for byte in digest)
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values]


class _OfflineEmbedder(EmbeddingAdapter):
    def __init__(self) -> None:
        super().__init__(model_name="test-deterministic-384", dimensions=384)

    async def encode_passage(self, text: str) -> list[float]:
        return _unit_vector(f"passage:{text}")

    async def encode_query(self, text: str) -> list[float]:
        return _unit_vector(f"query:{text}")


@pytest.fixture
def client(tmp_path, monkeypatch, isolated_provider_credentials):
    from app.db import reset_initialization_cache
    from app.main import app
    from app.services.memory import memory_service

    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    monkeypatch.setattr(memory_service, "embedder", _OfflineEmbedder())
    with patch(
        "app.main.sidecar_supervisor.ensure_available",
        new=AsyncMock(return_value=SimpleNamespace(url=None)),
    ), patch(
        "app.main.sidecar_supervisor.close",
        new=AsyncMock(),
    ):
        with TestClient(app) as test_client:
            yield test_client
    reset_initialization_cache()


def _session(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def _get(client: TestClient, **params: object) -> object:
    query: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, list):
            for item in value:
                query.append((key, str(item)))
        elif value is not None:
            query.append((key, str(value)))
    return client.get("/api/memory/graph/projection", params=query)


def _code(response) -> str:
    detail = response.json().get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code") or "")
    return ""


def _seed_topic(session_id: str, label: str, identity_mode: str = "self") -> str:
    from app.db import get_db
    from app.services.memory_v11.topics import prepare_topic_label, resolve_or_create_topic

    prepared = prepare_topic_label(label)
    assert prepared is not None
    display, norm = prepared

    async def _run() -> str:
        db = await get_db("steins_gate", "memory")
        try:
            await db.execute("BEGIN IMMEDIATE")
            topic_id = await resolve_or_create_topic(
                db,
                session_id=session_id,
                identity_mode=identity_mode,
                display_label=display,
                normalized_label=norm,
            )
            await db.commit()
            return topic_id
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    return asyncio.run(_run())


def _seed_fact(
    session_id: str,
    text: str,
    *,
    topic_id: str | None = None,
    is_pinned: bool = False,
    identity_mode: str = "self",
    worldline: str = "steins_gate",
) -> str:
    from app.services.memory_v11.repository import create_stable_fact

    row = asyncio.run(
        create_stable_fact(
            session_id=session_id,
            worldline=worldline,
            identity_mode=identity_mode,
            display_text=text,
            semantic_json={"subject": "user", "predicate": "states", "object": {"text": text}},
            topic_id=topic_id,
            is_pinned=is_pinned,
        )
    )
    return str(row["fact_id"])


def _seed_experience(
    session_id: str,
    text: str,
    *,
    is_pinned: bool = False,
    identity_mode: str = "self",
    worldline: str = "steins_gate",
    updated_at: str = "2026-08-01T00:00:00+00:00",
) -> str:
    from app.db import get_db

    async def _run() -> str:
        db = await get_db(worldline, "memory")
        try:
            observation_id = str(uuid4())
            experience_id = str(uuid4())
            semantic = {"subject": "user", "predicate": "event", "object": text[:24]}
            fingerprint = hashlib.sha256(
                json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            await db.execute(
                """INSERT INTO memory_observations(
                       observation_id, session_id, identity_mode, display_text,
                       semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                       confidence, topic_label_proposal, status, extractor_version,
                       reanalysis_count, expires_at, created_at, updated_at
                   ) VALUES(?,?,?,?,?,?, 'direct_user', 'episodic', 0.9, NULL,
                            'attached', 'memory-v11-1', 0, NULL, ?, ?)""",
                (
                    observation_id,
                    session_id,
                    identity_mode,
                    text,
                    json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fingerprint,
                    updated_at,
                    updated_at,
                ),
            )
            await db.execute(
                """INSERT INTO memory_observation_sources(
                       observation_id, source_message_id, conversation_id, source_role,
                       source_fingerprint, source_created_at, source_state, excerpt
                   ) VALUES(?,?,?, 'user', ?, ?, 'present', NULL)""",
                (observation_id, int(uuid4().int % 10**9), "conv-proj", f"fp-{observation_id[:8]}", updated_at),
            )
            await db.execute(
                """INSERT INTO experiences(
                       experience_id, session_id, conversation_id, identity_mode,
                       observation_id, display_text, semantic_json, semantic_fingerprint,
                       confidence, status, expires_at, created_at, updated_at, deleted_at,
                       is_pinned
                   ) VALUES(?,?,?,?,?,?,?,?,0.9,'active',NULL,?,?,NULL,?)""",
                (
                    experience_id,
                    session_id,
                    "conv-proj",
                    identity_mode,
                    observation_id,
                    text,
                    json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fingerprint,
                    updated_at,
                    updated_at,
                    1 if is_pinned else 0,
                ),
            )
            await db.commit()
            return experience_id
        finally:
            await db.close()

    return asyncio.run(_run())


def test_validation_matrix_uses_frozen_codes(client: TestClient) -> None:
    sid = _session("val")
    base = {"session_id": sid, "worldline": "steins_gate", "identity_mode": "self", "view": "overview"}

    assert _get(client, worldline="steins_gate", identity_mode="self", view="overview").status_code == 422
    assert _code(_get(client, worldline="steins_gate", identity_mode="self", view="overview")) == "empty_session_id"

    assert _code(_get(client, session_id=sid, identity_mode="self", view="overview")) == "missing_worldline"
    assert _code(_get(client, session_id=sid, worldline="alpha", identity_mode="self", view="overview")) == "invalid_worldline"
    assert _code(_get(client, session_id=sid, worldline="steins_gate", view="overview")) == "missing_identity_mode"
    assert _code(_get(client, session_id=sid, worldline="steins_gate", identity_mode="kuri", view="overview")) == "invalid_identity_mode"
    assert _code(_get(client, session_id=sid, worldline="steins_gate", identity_mode="self")) == "missing_view"
    assert _code(
        _get(client, session_id=sid, worldline="steins_gate", identity_mode="self", view="global")
    ) == "invalid_view"

    local = _get(client, session_id=sid, worldline="steins_gate", identity_mode="self", view="local")
    assert local.status_code == 422
    assert _code(local) == "projection_view_unavailable"

    extra = _get(client, **base, node_limit="10")
    assert extra.status_code == 422
    assert _code(extra) == "invalid_projection_parameter"

    long_q = _get(client, **base, query="x" * 201)
    assert _code(long_q) == "query_too_long"

    kind = _get(client, **base, kind="person")
    assert _code(kind) == "invalid_kind"

    pin = _get(client, **base, pinned_only="yes")
    assert _code(pin) == "invalid_pinned_only"

    rng = _get(client, **base, updated_from="2026-08-02T00:00:00Z", updated_to="2026-08-01T00:00:00Z")
    assert _code(rng) == "invalid_time_range"


def test_empty_scope_still_centers_soul(client: TestClient) -> None:
    sid = _session("empty")
    response = _get(
        client,
        session_id=sid,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["empty"] is True
    assert body["eligible"]["results"] == 0
    assert body["projection_version"] == "memory-projection-v1"
    assert body["center"]["kind"] == "continuity_hub"
    assert body["center"]["label_secondary"] == "SOUL"
    assert body["center"]["projection_id"] == f"hub:continuity:{sid}:steins_gate:self"
    assert body["shown"]["nodes"] == len(body["nodes"])
    assert body["nodes"][0]["projection_id"] == body["center"]["projection_id"]
    assert body["composition"]["person_anchors_supported"] is False


def test_scope_isolation_and_replay(client: TestClient) -> None:
    sid_a = _session("iso-a")
    sid_b = _session("iso-b")
    topic = _seed_topic(sid_a, "动漫")
    _seed_fact(sid_a, "喜欢看动漫", topic_id=topic)
    _seed_fact(sid_b, "other-session-fact")

    first = _get(
        client,
        session_id=sid_a,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    )
    assert first.status_code == 200, first.text
    body = first.json()
    labels = {node.get("label") for node in body["nodes"] if node["kind"] != "continuity_hub"}
    assert "喜欢看动漫" in labels
    assert "other-session-fact" not in labels

    okabe = _get(
        client,
        session_id=sid_a,
        worldline="steins_gate",
        identity_mode="okabe",
        view="overview",
    )
    assert okabe.json()["empty"] is True

    beta = _get(
        client,
        session_id=sid_a,
        worldline="beta",
        identity_mode="self",
        view="overview",
    )
    assert beta.json()["empty"] is True

    second = _get(
        client,
        session_id=sid_a,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    )
    a = first.json()
    b = second.json()
    assert [n["projection_id"] for n in a["nodes"]] == [n["projection_id"] for n in b["nodes"]]
    assert [(e["kind"], e["from"], e["to"]) for e in a["edges"]] == [
        (e["kind"], e["from"], e["to"]) for e in b["edges"]
    ]
    assert a["result_ids"] == b["result_ids"]


def test_backbone_and_no_dangling_edges(client: TestClient) -> None:
    sid = _session("back")
    topic = _seed_topic(sid, "咖啡")
    _seed_fact(sid, "喜欢黑咖啡", topic_id=topic)
    _seed_experience(sid, "昨天喝了手冲")

    body = _get(
        client,
        session_id=sid,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    ).json()
    ids = {node["projection_id"] for node in body["nodes"]}
    hub = body["center"]["projection_id"]
    assert hub in ids
    for edge in body["edges"]:
        assert edge["from"] in ids
        assert edge["to"] in ids
        assert edge["kind"] in {"hub_to_anchor", "has_topic", "hub_to_evidence"}

    undirected: dict[str, set[str]] = {node_id: set() for node_id in ids}
    for edge in body["edges"]:
        undirected[edge["from"]].add(edge["to"])
        undirected[edge["to"]].add(edge["from"])
    seen = {hub}
    stack = [hub]
    while stack:
        cur = stack.pop()
        for nxt in undirected[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    assert seen == ids

    topic_nodes = [n for n in body["nodes"] if n["kind"] == "topic"]
    assert topic_nodes
    for topic_node in topic_nodes:
        assert any(
            e["kind"] == "hub_to_anchor" and e["to"] == topic_node["projection_id"]
            for e in body["edges"]
        )


def test_pinned_experience_ranks_with_pinned_facts(client: TestClient) -> None:
    sid = _session("pin")
    _seed_fact(sid, "unpinned-fact", is_pinned=False)
    pinned_exp = _seed_experience(
        sid,
        "pinned-experience",
        is_pinned=True,
        updated_at="2026-08-10T00:00:00+00:00",
    )
    _seed_experience(
        sid,
        "old-unpinned-experience",
        is_pinned=False,
        updated_at="2026-07-01T00:00:00+00:00",
    )

    body = _get(
        client,
        session_id=sid,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    ).json()
    assert body["result_ids"][0] == f"experience:{pinned_exp}"
    exp_node = next(n for n in body["nodes"] if n["kind"] == "experience" and n["experience_id"] == pinned_exp)
    assert exp_node["is_pinned"] is True


def test_search_is_literal_and_criteria_echoed(client: TestClient) -> None:
    sid = _session("q")
    _seed_fact(sid, "喜欢黑咖啡")
    _seed_fact(sid, "percent_100")
    body = _get(
        client,
        session_id=sid,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
        query="黑咖啡",
    ).json()
    assert body["criteria"]["query"] == "黑咖啡"
    labels = [n.get("label") for n in body["nodes"] if n["kind"] == "fact"]
    assert labels == ["喜欢黑咖啡"]
    wildcard = _get(
        client,
        session_id=sid,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
        query="%",
    ).json()
    assert "percent_100" not in [n.get("label") for n in wildcard["nodes"]]


def test_experience_pin_column_backfill(tmp_path, monkeypatch) -> None:
    from app.db import _ensure_experiences_is_pinned, get_data_root, reset_initialization_cache

    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    memory_path = tmp_path / "steins_gate" / "memory.sqlite3"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(memory_path)
    try:
        connection.execute(
            """CREATE TABLE experiences (
                experience_id TEXT PRIMARY KEY,
                session_id TEXT,
                conversation_id TEXT,
                identity_mode TEXT,
                observation_id TEXT,
                display_text TEXT,
                semantic_json TEXT,
                semantic_fingerprint TEXT,
                confidence REAL,
                status TEXT,
                expires_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                deleted_at TEXT
            )"""
        )
        connection.execute(
            """INSERT INTO experiences(
                   experience_id, session_id, conversation_id, identity_mode,
                   observation_id, display_text, semantic_json, semantic_fingerprint,
                   confidence, status, created_at, updated_at
               ) VALUES('exp-old','s','c','self','obs','old','{}','fp',0.5,'active','t','t')"""
        )
        connection.commit()
        _ensure_experiences_is_pinned(connection)
        connection.commit()
        cols = {row[1] for row in connection.execute("PRAGMA table_info(experiences)")}
        assert "is_pinned" in cols
        pinned = connection.execute(
            "SELECT is_pinned FROM experiences WHERE experience_id='exp-old'"
        ).fetchone()[0]
        assert int(pinned) == 0
    finally:
        connection.close()
    reset_initialization_cache()
    _ = get_data_root()


def test_projection_route_does_not_import_provider(client: TestClient, monkeypatch) -> None:
    import app.services.memory_v11.projection as projection

    def boom(*_args, **_kwargs):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr("app.services.provider_runtime.complete", boom, raising=False)
    sid = _session("noprov")
    _seed_fact(sid, "no-provider")
    response = _get(
        client,
        session_id=sid,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    )
    assert response.status_code == 200
    assert projection.PROJECTION_VERSION == "memory-projection-v1"
