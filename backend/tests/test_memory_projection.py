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


G_STORE_LEAK_SENTINEL = "G3-LEAK-SENTINEL"
G_STORE_LEAK_SQL = "SELECT leak FROM g3_forbidden"
G_STORE_LEAK_REPR = "OperationalError('planted-repr')"


def _install_unavailable_store(
    monkeypatch, tmp_path, failure: str, *, plant_leak: bool = False
) -> str:
    """Patch projection.get_db with a governed-store open or read failure.

    G1/G2 use the native aiosqlite open error (codes preserved).
    G3 may wrap that open/read error so the underlying exception carries
    leak sentinels; the public HTTP body must not echo them.
    """
    import aiosqlite

    import app.services.memory_v11.projection as projection

    real_get_db = projection.get_db
    blocker = tmp_path / "store-open-blocker"
    leak = ""
    if plant_leak:
        leak = (
            f"{G_STORE_LEAK_SENTINEL} path={blocker} sql={G_STORE_LEAK_SQL} "
            f"{G_STORE_LEAK_REPR}"
        )

    async def get_db_open_unavailable(
        worldline: str = "steins_gate", kind: str = "history"
    ):
        if kind != "memory":
            return await real_get_db(worldline, kind)
        # Path exists as a directory so this is not a missing-file fixture.
        blocker.mkdir(exist_ok=True)
        if not plant_leak:
            return await aiosqlite.connect(str(blocker))
        try:
            return await aiosqlite.connect(str(blocker))
        except sqlite3.OperationalError as exc:
            raise sqlite3.OperationalError(f"{exc}; unable to open; {leak}") from None

    async def get_db_read_unavailable(
        worldline: str = "steins_gate", kind: str = "history"
    ):
        db = await real_get_db(worldline, kind)
        if kind != "memory":
            return db

        async def execute(sql, parameters=None):
            if plant_leak:
                raise sqlite3.OperationalError(f"disk I/O error; {leak}")
            raise sqlite3.OperationalError("disk I/O error")

        db.execute = execute
        return db

    monkeypatch.setattr(
        projection,
        "get_db",
        get_db_open_unavailable if failure == "open" else get_db_read_unavailable,
    )
    return leak


def _unavailable_store_response(client: TestClient, prefix: str):
    return _get(
        client,
        session_id=_session(prefix),
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    )


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


@pytest.mark.parametrize("failure", ["open", "read"])
def test_unavailable_store_returns_503(
    client: TestClient, monkeypatch, tmp_path, failure: str
) -> None:
    """G1: governed-store open/read unavailable → 503 projection_unavailable."""
    _install_unavailable_store(monkeypatch, tmp_path, failure)
    response = _unavailable_store_response(client, "g1-store")
    assert response.status_code == 503, (
        f"G1 {failure} expected 503, got {response.status_code}: {response.text}"
    )
    assert _code(response) == "projection_unavailable", (
        f"G1 {failure} expected projection_unavailable, got {_code(response)!r}: "
        f"{response.text}"
    )


@pytest.mark.parametrize("failure", ["open", "read"])
def test_storage_failure_is_not_200_empty(
    client: TestClient, monkeypatch, tmp_path, failure: str
) -> None:
    """G2: storage unavailable is not a 200 response with empty=true."""
    _install_unavailable_store(monkeypatch, tmp_path, failure)
    response = _unavailable_store_response(client, "g2-store")
    body = response.json()
    assert response.status_code != 200, (
        f"G2 {failure} must not be 200: {response.status_code} {response.text}"
    )
    assert body.get("empty") is not True, (
        f"G2 {failure} must not set empty=true: {response.text}"
    )


@pytest.mark.parametrize("failure", ["open", "read"])
def test_storage_error_body_is_stable(
    client: TestClient, monkeypatch, tmp_path, failure: str
) -> None:
    """G3: storage unavailable returns a stable short detail, with no leak."""
    leak = _install_unavailable_store(
        monkeypatch, tmp_path, failure, plant_leak=True
    )
    assert G_STORE_LEAK_SENTINEL in leak
    assert G_STORE_LEAK_SQL in leak
    assert G_STORE_LEAK_REPR in leak
    response = _unavailable_store_response(client, "g3-store")
    body = response.json()
    assert body == {
        "detail": {
            "code": "projection_unavailable",
            "message": "memory store unavailable",
        }
    }, f"G3 {failure} unexpected public body: {response.text}"
    message = body["detail"]["message"]
    assert isinstance(message, str) and 0 < len(message) <= 40
    public = response.text
    assert G_STORE_LEAK_SENTINEL not in public
    assert G_STORE_LEAK_SQL not in public
    assert G_STORE_LEAK_REPR not in public
    assert str(tmp_path / "store-open-blocker") not in public
    assert "disk I/O error" not in public
    assert "unable to open" not in public
    assert "Traceback" not in public


def test_program_operational_error_stays_projection_failed(
    client: TestClient, monkeypatch
) -> None:
    """Unrecognized OperationalError stays 500; not mapped to 503."""
    import app.services.memory_v11.projection as projection

    sid = _session("g1-sql-bug")
    real_get_db = projection.get_db

    async def get_db_program_sql_error(
        worldline: str = "steins_gate", kind: str = "history"
    ):
        db = await real_get_db(worldline, kind)
        if kind != "memory":
            return db

        async def execute(sql, parameters=None):
            raise sqlite3.OperationalError("ambiguous column name")

        db.execute = execute
        return db

    monkeypatch.setattr(projection, "get_db", get_db_program_sql_error)
    response = _get(
        client,
        session_id=sid,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    )
    assert response.status_code == 500, (
        "program OperationalError must stay 500, "
        f"got {response.status_code}: {response.text}"
    )
    assert _code(response) == "projection_failed", (
        "program OperationalError must stay projection_failed, "
        f"got {_code(response)!r}: {response.text}"
    )


C_OVERSIZE_ELIGIBLE = 80
C_NODE_BUDGET = 64
C_EDGE_BUDGET = 128
C_HARD_MAX_NODES = 160
C_HARD_MAX_EDGES = 320
C_COUNT_KEYS = ("nodes", "edges", "records", "results")


def _seed_oversize_overview_scope() -> str:
    """Standalone experiences only: hub + 80 results exceeds the 64-node budget."""
    sid = _session("c-oversize")
    for index in range(C_OVERSIZE_ELIGIBLE):
        _seed_experience(sid, f"C-oversize-{index:03d}", is_pinned=False)
    return sid


def _get_overview_body(client: TestClient, session_id: str) -> dict:
    response = _get(
        client,
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        view="overview",
    )
    assert response.status_code == 200, (
        f"overview GET must be 200, got {response.status_code}: {response.text}"
    )
    return response.json()


def _nodes_of(body: dict, kind: str) -> list[dict]:
    return [node for node in body["nodes"] if node["kind"] == kind]


def _primary_edges(body: dict, *, kind: str, node_id: str) -> list[dict]:
    if kind == "hub_to_anchor":
        return [
            edge
            for edge in body["edges"]
            if edge["kind"] == "hub_to_anchor" and edge["to"] == node_id
        ]
    if kind == "has_topic":
        return [
            edge
            for edge in body["edges"]
            if edge["kind"] == "has_topic" and edge["from"] == node_id
        ]
    if kind == "hub_to_evidence":
        return [
            edge
            for edge in body["edges"]
            if edge["kind"] == "hub_to_evidence" and edge["to"] == node_id
        ]
    raise AssertionError(f"unknown primary edge kind: {kind!r}")


def test_count_groups_coherent(client: TestClient) -> None:
    """C4: truncated populated envelope keeps shown/eligible/truncated coherent."""
    body = _get_overview_body(client, _seed_oversize_overview_scope())
    assert body["empty"] is False
    shown = body["shown"]
    eligible = body["eligible"]
    truncated = body["truncated"]
    assert shown["nodes"] == len(body["nodes"])
    assert shown["edges"] == len(body["edges"])
    assert len(body["result_ids"]) == shown["results"]
    assert any(truncated[key] is True for key in C_COUNT_KEYS), (
        "C4 fixture must truncate at least one count group: "
        f"eligible={eligible} shown={shown} truncated={truncated}"
    )
    for key in C_COUNT_KEYS:
        assert truncated[key] == (eligible[key] > shown[key]), (
            f"C4 truncated.{key} != (eligible.{key} > shown.{key}): "
            f"eligible={eligible} shown={shown} truncated={truncated}"
        )


def test_default_overview_budget_64_128(client: TestClient) -> None:
    """C5: default Overview budget is 64/128 and actually binds on oversize."""
    body = _get_overview_body(client, _seed_oversize_overview_scope())
    shown = body["shown"]
    budgets = body["budgets"]
    assert body["eligible"]["nodes"] > C_NODE_BUDGET, (
        "C5 fixture must exceed the node budget: "
        f"eligible.nodes={body['eligible']['nodes']}"
    )
    assert shown["nodes"] <= C_NODE_BUDGET
    assert shown["edges"] <= C_EDGE_BUDGET
    assert shown["nodes"] == C_NODE_BUDGET, (
        "C5 node budget was not reached: "
        f"shown.nodes={shown['nodes']} eligible={body['eligible']}"
    )
    assert budgets["nodes"] == C_NODE_BUDGET
    assert budgets["edges"] == C_EDGE_BUDGET


def test_hard_max_reported_not_used_as_silent_raise(client: TestClient) -> None:
    """C6: hard max is reported 160/320 and is not the live admission budget."""
    body = _get_overview_body(client, _seed_oversize_overview_scope())
    shown = body["shown"]
    budgets = body["budgets"]
    eligible_nodes = body["eligible"]["nodes"]
    assert budgets["hard_max_nodes"] == C_HARD_MAX_NODES
    assert budgets["hard_max_edges"] == C_HARD_MAX_EDGES
    assert shown["nodes"] <= budgets["nodes"]
    assert shown["edges"] <= budgets["edges"]
    assert eligible_nodes > budgets["nodes"], (
        "C6 fixture must exceed the live node budget: "
        f"eligible.nodes={eligible_nodes} budgets.nodes={budgets['nodes']}"
    )
    assert shown["nodes"] == budgets["nodes"], (
        "C6 live admission used something other than the default budget: "
        f"shown.nodes={shown['nodes']} budgets={budgets} eligible={body['eligible']}"
    )
    assert shown["nodes"] < budgets["hard_max_nodes"]


C1_PINNED_STANDALONE = 62
C1_TOPIC_LABEL = "C1-atomic-topic"
C1_FACT_LABEL = "C1-atomic-fact"
C2_TOPIC_LABEL = "C2-backbone-topic"
C2_FACT_LABEL = "C2-backbone-fact"
C3_TOPIC_LABEL = "C3-optional-topic"
C3_FACT_LABEL = "C3-optional-fact"
C3_EXPERIENCE_LABEL = "C3-optional-experience"


def test_node_and_mandatory_edge_cost_atomic(client: TestClient) -> None:
    """C1: leftover=1 cannot admit a Topic+Fact bundle piecemeal."""
    sid = _session("c1-atomic")
    for index in range(C1_PINNED_STANDALONE):
        _seed_experience(
            sid,
            f"C1-pinned-{index:02d}",
            is_pinned=True,
        )
    topic_id = _seed_topic(sid, C1_TOPIC_LABEL)
    fact_id = _seed_fact(sid, C1_FACT_LABEL, topic_id=topic_id, is_pinned=False)
    fact_pid = f"fact:{fact_id}"
    topic_pid = f"topic:{topic_id}"

    body = _get_overview_body(client, sid)
    assert body["empty"] is False
    assert body["eligible"]["results"] == C1_PINNED_STANDALONE + 1
    assert body["eligible"]["nodes"] == C_NODE_BUDGET + 1
    assert body["shown"]["nodes"] == C_NODE_BUDGET - 1, (
        "C1 leftover=1 fixture did not leave one live node slot after pinned "
        f"fill: shown={body['shown']} eligible={body['eligible']}"
    )
    assert body["shown"]["results"] == C1_PINNED_STANDALONE

    labels = {node.get("label") for node in body["nodes"]}
    kinds = {node["kind"] for node in body["nodes"]}
    assert C1_TOPIC_LABEL not in labels
    assert C1_FACT_LABEL not in labels
    assert "topic" not in kinds
    assert "fact" not in kinds
    assert fact_pid not in body["result_ids"]
    assert topic_pid not in body["result_ids"]
    assert all(
        node.get("is_pinned") is True
        for node in _nodes_of(body, "experience")
    )

    edge_kinds = {edge["kind"] for edge in body["edges"]}
    assert "hub_to_anchor" not in edge_kinds
    assert "has_topic" not in edge_kinds
    node_ids = {node["projection_id"] for node in body["nodes"]}
    for edge in body["edges"]:
        assert edge["from"] in node_ids
        assert edge["to"] in node_ids


def test_mandatory_backbone_never_truncated(client: TestClient) -> None:
    """C2: every shown non-hub node keeps exactly one mandatory primary edge."""
    sid = _session("c2-back")
    topic_id = _seed_topic(sid, C2_TOPIC_LABEL)
    fact_id = _seed_fact(sid, C2_FACT_LABEL, topic_id=topic_id, is_pinned=True)
    for index in range(C_OVERSIZE_ELIGIBLE):
        _seed_experience(sid, f"C2-stand-{index:03d}", is_pinned=False)

    body = _get_overview_body(client, sid)
    assert body["empty"] is False
    assert body["truncated"]["nodes"] is True
    topics = _nodes_of(body, "topic")
    facts = _nodes_of(body, "fact")
    experiences = _nodes_of(body, "experience")
    assert topics, f"C2 must show a Topic: shown={body['shown']}"
    assert facts, f"C2 must show a topic-backed Fact: shown={body['shown']}"
    assert experiences, f"C2 must show a standalone Experience: shown={body['shown']}"
    assert any(node.get("label") == C2_TOPIC_LABEL for node in topics)
    assert any(node.get("label") == C2_FACT_LABEL for node in facts)
    node_ids = {node["projection_id"] for node in body["nodes"]}
    hub = body["center"]["projection_id"]

    for topic in topics:
        primaries = _primary_edges(
            body, kind="hub_to_anchor", node_id=topic["projection_id"]
        )
        assert len(primaries) == 1, topic
        assert primaries[0]["from"] == hub
        assert primaries[0]["from"] in node_ids
        assert primaries[0]["to"] in node_ids

    for fact in facts:
        primaries = _primary_edges(
            body, kind="has_topic", node_id=fact["projection_id"]
        )
        assert len(primaries) == 1, fact
        assert primaries[0]["to"] == f"topic:{fact['topic_id']}"
        assert primaries[0]["from"] in node_ids
        assert primaries[0]["to"] in node_ids
        assert not _primary_edges(
            body, kind="hub_to_evidence", node_id=fact["projection_id"]
        )

    for experience in experiences:
        primaries = _primary_edges(
            body, kind="hub_to_evidence", node_id=experience["projection_id"]
        )
        assert len(primaries) == 1, experience
        assert primaries[0]["from"] == hub
        assert primaries[0]["from"] in node_ids
        assert primaries[0]["to"] in node_ids
        assert not _primary_edges(
            body, kind="has_topic", node_id=experience["projection_id"]
        )


def test_optional_associations_cut_first(client: TestClient) -> None:
    """C3: Spine emits no optional/extra associations; do not invent them."""
    sid = _session("c3-opt")
    topic_id = _seed_topic(sid, C3_TOPIC_LABEL)
    fact_id = _seed_fact(sid, C3_FACT_LABEL, topic_id=topic_id)
    experience_id = _seed_experience(sid, C3_EXPERIENCE_LABEL)
    fact_pid = f"fact:{fact_id}"
    experience_pid = f"experience:{experience_id}"

    body = _get_overview_body(client, sid)
    assert body["empty"] is False
    fact_labels = {node.get("label") for node in _nodes_of(body, "fact")}
    experience_labels = {node.get("label") for node in _nodes_of(body, "experience")}
    assert C3_FACT_LABEL in fact_labels
    assert C3_EXPERIENCE_LABEL in experience_labels

    for edge in body["edges"]:
        assert edge["kind"] in {"hub_to_anchor", "has_topic", "hub_to_evidence"}

    hub = body["center"]["projection_id"]
    topic_pid = f"topic:{topic_id}"
    node_ids = {node["projection_id"] for node in body["nodes"]}
    topics = _nodes_of(body, "topic")
    assert len(topics) == 1
    assert topics[0]["projection_id"] == topic_pid
    topic_anchors = _primary_edges(
        body, kind="hub_to_anchor", node_id=topic_pid
    )
    assert len(topic_anchors) == 1
    assert topic_anchors[0]["from"] == hub
    assert topic_anchors[0]["to"] == topic_pid

    fact_topics = _primary_edges(body, kind="has_topic", node_id=fact_pid)
    assert len(fact_topics) == 1
    assert fact_topics[0]["to"] == topic_pid
    assert not _primary_edges(body, kind="hub_to_evidence", node_id=fact_pid)

    experience_hubs = _primary_edges(
        body, kind="hub_to_evidence", node_id=experience_pid
    )
    assert len(experience_hubs) == 1
    assert experience_hubs[0]["from"] == hub
    assert not _primary_edges(body, kind="has_topic", node_id=experience_pid)

    for edge in body["edges"]:
        assert edge["from"] in node_ids
        assert edge["to"] in node_ids
    assert len(body["edges"]) == len(body["nodes"]) - 1
