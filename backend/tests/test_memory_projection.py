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


A2_T0_FACT = "A2-T0-fact"
A2_T0_EXPERIENCE = "A2-T0-experience"
A2_T1_FACT = "A2-T1-fact"
A2_T1_EXPERIENCE = "A2-T1-experience"
_A2_CONTROL_SQL = frozenset(
    {"BEGIN", "COMMIT", "ROLLBACK", "PRAGMA", "SAVEPOINT", "RELEASE"}
)


def _a2_sql_verb(sql: object) -> str:
    if not isinstance(sql, str):
        return ""
    stripped = sql.lstrip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0].upper()


def test_projection_no_mixed_era_under_concurrent_write(
    client: TestClient, monkeypatch
) -> None:
    """A2: one GET must be entirely T0 or entirely T1, never mixed-era."""
    import threading

    from app.db import _resolve_path
    import app.services.memory_v11.projection as projection

    sid = _session("a2-snap")
    fact_id = _seed_fact(sid, A2_T0_FACT)
    experience_id = _seed_experience(sid, A2_T0_EXPERIENCE)
    memory_path = _resolve_path("steins_gate", "memory")

    first_data_read = threading.Event()
    t1_committed = threading.Event()
    writer_errors: list[BaseException] = []

    def write_t1() -> None:
        try:
            if not first_data_read.wait(timeout=5.0):
                raise TimeoutError("projection never issued a data read")
            connection = sqlite3.connect(memory_path, timeout=10.0)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE stable_fact_versions SET display_text=? WHERE fact_id=?",
                    (A2_T1_FACT, fact_id),
                )
                connection.execute(
                    "UPDATE experiences SET display_text=? WHERE experience_id=?",
                    (A2_T1_EXPERIENCE, experience_id),
                )
                connection.commit()
            finally:
                connection.close()
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            t1_committed.set()

    real_get_db = projection.get_db

    async def get_db_with_test_barrier(worldline: str = "steins_gate", kind: str = "history"):
        db = await real_get_db(worldline, kind)
        real_execute = db.execute

        async def execute(sql, parameters=None):
            result = (
                await real_execute(sql)
                if parameters is None
                else await real_execute(sql, parameters)
            )
            # Test-only barrier after the first non-control statement.
            # Does not name tables, aliases, or a required read order.
            if (
                _a2_sql_verb(sql) not in _A2_CONTROL_SQL
                and not first_data_read.is_set()
            ):
                first_data_read.set()
                if not t1_committed.wait(timeout=5.0):
                    raise TimeoutError("T1 writer did not commit in the read window")
            return result

        db.execute = execute
        return db

    monkeypatch.setattr(projection, "get_db", get_db_with_test_barrier)
    writer = threading.Thread(target=write_t1, name="a2-t1-writer")
    writer.start()
    try:
        response = _get(
            client,
            session_id=sid,
            worldline="steins_gate",
            identity_mode="self",
            view="overview",
        )
    finally:
        writer.join(timeout=10.0)

    assert not writer_errors, f"T1 writer failed: {writer_errors!r}"
    assert writer.is_alive() is False
    assert response.status_code == 200, response.text
    body = response.json()

    fact_labels = {node.get("label") for node in body["nodes"] if node["kind"] == "fact"}
    experience_labels = {
        node.get("label") for node in body["nodes"] if node["kind"] == "experience"
    }
    all_labels = fact_labels | experience_labels
    result_ids = list(body["result_ids"])

    entirely_t0 = fact_labels == {A2_T0_FACT} and experience_labels == {A2_T0_EXPERIENCE}
    entirely_t1 = fact_labels == {A2_T1_FACT} and experience_labels == {A2_T1_EXPERIENCE}
    assert entirely_t0 or entirely_t1, (
        "mixed-era projection: "
        f"fact_labels={sorted(fact_labels)} "
        f"experience_labels={sorted(experience_labels)} "
        f"result_ids={result_ids} "
        f"shown={body.get('shown')} eligible={body.get('eligible')}"
    )
    assert A2_T0_FACT not in all_labels or A2_T1_EXPERIENCE not in all_labels
    assert A2_T1_FACT not in all_labels or A2_T0_EXPERIENCE not in all_labels
    assert body["shown"]["nodes"] == len(body["nodes"])
    assert body["shown"]["edges"] == len(body["edges"])
    assert len(result_ids) == body["shown"]["results"]
    assert f"fact:{fact_id}" in result_ids
    assert f"experience:{experience_id}" in result_ids


B2_TOPIC_ALPHA = "B2-anchor-alpha"
B2_TOPIC_BETA = "B2-anchor-beta"
B2_SUPPORT_ALPHA = "B2-support-alpha"
B2_SUPPORT_BETA = "B2-support-beta"
B2_OLD_AT = "2020-01-01T00:00:00+00:00"
B2_NEW_AT = "2026-08-01T00:00:00+00:00"
# Hub consumes 1 of 64. Recency-first standalone experiences must fill the
# remaining 63 slots so older topic bundles (2 nodes each) cannot enter.
B2_RECENCY_COUNT = 63
B2_SHOWN_RECENCY = 59
B2_NODE_BUDGET = 64
B2_RECENCY_PREFIX = "B2-recency-"


def _set_fact_updated_at(fact_id: str, updated_at: str) -> None:
    from app.db import _resolve_path

    connection = sqlite3.connect(_resolve_path("steins_gate", "memory"))
    try:
        connection.execute(
            "UPDATE stable_facts SET updated_at=? WHERE fact_id=?",
            (updated_at, fact_id),
        )
        connection.commit()
    finally:
        connection.close()


def test_anchor_coverage_before_recency_fill(client: TestClient) -> None:
    """B2: coverable Topic bundles must take capacity before recency fill."""
    sid = _session("b2-cover")
    topic_alpha = _seed_topic(sid, B2_TOPIC_ALPHA)
    topic_beta = _seed_topic(sid, B2_TOPIC_BETA)
    support_alpha = _seed_fact(sid, B2_SUPPORT_ALPHA, topic_id=topic_alpha)
    support_beta = _seed_fact(sid, B2_SUPPORT_BETA, topic_id=topic_beta)
    _set_fact_updated_at(support_alpha, B2_OLD_AT)
    _set_fact_updated_at(support_beta, B2_OLD_AT)
    for index in range(B2_RECENCY_COUNT):
        _seed_experience(
            sid,
            f"B2-recency-{index:02d}",
            is_pinned=False,
            updated_at=B2_NEW_AT,
        )

    body = _get(
        client,
        session_id=sid,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    ).json()

    topic_nodes = [node for node in body["nodes"] if node["kind"] == "topic"]
    fact_nodes = [node for node in body["nodes"] if node["kind"] == "fact"]
    topic_labels = {node["label"] for node in topic_nodes}
    fact_labels = {node["label"] for node in fact_nodes}
    node_ids = {node["projection_id"] for node in body["nodes"]}
    hub = body["center"]["projection_id"]

    assert {B2_TOPIC_ALPHA, B2_TOPIC_BETA} <= topic_labels, (
        "coverable Topic anchors lost to recency fill: "
        f"topic_labels={sorted(topic_labels)} "
        f"fact_labels={sorted(fact_labels)} "
        f"shown={body.get('shown')}"
    )
    assert {B2_SUPPORT_ALPHA, B2_SUPPORT_BETA} <= fact_labels

    for topic_node in topic_nodes:
        assert any(
            fact["topic_id"] == topic_node["topic_id"] for fact in fact_nodes
        ), f"orphan topic without supporting fact: {topic_node['label']}"
        assert any(
            edge["kind"] == "hub_to_anchor" and edge["to"] == topic_node["projection_id"]
            for edge in body["edges"]
        )

    for edge in body["edges"]:
        assert edge["from"] in node_ids
        assert edge["to"] in node_ids

    undirected: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in body["edges"]:
        undirected[edge["from"]].add(edge["to"])
        undirected[edge["to"]].add(edge["from"])
    seen = {hub}
    stack = [hub]
    while stack:
        current = stack.pop()
        for nxt in undirected[current]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    assert seen == node_ids
    assert body["shown"]["nodes"] == len(body["nodes"])
    assert body["shown"]["edges"] == len(body["edges"])

    recency_nodes = [
        node
        for node in body["nodes"]
        if node["kind"] == "experience"
        and str(node.get("label") or "").startswith(B2_RECENCY_PREFIX)
    ]
    assert body["shown"]["nodes"] == B2_NODE_BUDGET, (
        "remaining node capacity not filled after anchor coverage: "
        f"shown={body.get('shown')} recency={len(recency_nodes)}"
    )
    assert len(recency_nodes) == B2_SHOWN_RECENCY, (
        "recency fill did not occupy leftover capacity: "
        f"standalone_experiences={len(recency_nodes)} shown={body.get('shown')}"
    )
