"""Canonical Constellation Overview projection (S4-MEMORY-SPINE, G61).

Provider-free. Deterministic. Exact-scope. Reads governed SQLite only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.db import get_db
from app.services.memory_v11.contracts import MemoryValidationError

PROJECTION_VERSION = "memory-projection-v1"
OVERVIEW_NODE_BUDGET = 64
OVERVIEW_EDGE_BUDGET = 128
HARD_MAX_NODES = 160
HARD_MAX_EDGES = 320
ALLOWED_WORLDLINES = frozenset({"steins_gate", "beta"})
ALLOWED_IDENTITY_MODES = frozenset({"self", "okabe"})
ALLOWED_VIEWS = frozenset({"overview", "local"})
ALLOWED_KINDS = frozenset({"topic", "fact", "experience"})
ALLOWED_QUERY_KEYS = frozenset(
    {
        "session_id",
        "worldline",
        "identity_mode",
        "view",
        "query",
        "kind",
        "topic_id",
        "pinned_only",
        "updated_from",
        "updated_to",
        "anchor_type",
        "anchor_id",
        "depth",
    }
)
KIND_NODE_RANK = {
    "continuity_hub": 0,
    "topic": 1,
    "fact": 2,
    "experience": 3,
}
KIND_EDGE_RANK = {
    "hub_to_anchor": 0,
    "has_topic": 1,
    "hub_to_evidence": 2,
}


class ProjectionError(MemoryValidationError):
    """Stable projection parameter / view error."""


_UNAVAILABLE_SQLITE_PRIMARY = frozenset(
    {
        int(getattr(sqlite3, "SQLITE_CANTOPEN", 14)),
        int(getattr(sqlite3, "SQLITE_BUSY", 5)),
        int(getattr(sqlite3, "SQLITE_LOCKED", 6)),
        int(getattr(sqlite3, "SQLITE_IOERR", 10)),
    }
)
_UNAVAILABLE_SQLITE_PRIMARY_NAMES = frozenset(
    {"CANTOPEN", "BUSY", "LOCKED", "IOERR"}
)
_UNAVAILABLE_MESSAGE_MARKERS = (
    "unable to open",
    "disk i/o",
    "database is locked",
    "database is busy",
)


def _is_unavailable_store_error(exc: BaseException) -> bool:
    # Open-boundary permission only. FileNotFoundError stays out: init may
    # create a missing file, and missing-file is not a G1 fixture.
    if isinstance(exc, PermissionError):
        return True
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in _UNAVAILABLE_SQLITE_PRIMARY
    name = getattr(exc, "sqlite_errorname", None)
    if isinstance(name, str) and name.startswith("SQLITE_"):
        parts = name.split("_")
        if len(parts) >= 2:
            return parts[1] in _UNAVAILABLE_SQLITE_PRIMARY_NAMES
    text = str(exc).casefold()
    return any(marker in text for marker in _UNAVAILABLE_MESSAGE_MARKERS)


def _raise_storage_unavailable_or_reraise(exc: BaseException) -> None:
    if not isinstance(exc, Exception) or isinstance(exc, ProjectionError):
        raise exc
    if _is_unavailable_store_error(exc):
        raise ProjectionError(
            "projection_unavailable",
            "memory store unavailable",
        ) from None
    raise exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_rfc3339(raw: str, field: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProjectionError(f"invalid_{field}", f"{field} must be RFC3339 UTC") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_row_time(raw: Any) -> datetime:
    if raw is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    text = str(raw)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_expired(status: Any, expires_at: Any, now: datetime) -> bool:
    if str(status or "") == "expired":
        return True
    if expires_at is None:
        return False
    try:
        return _parse_row_time(expires_at) <= now
    except (TypeError, ValueError):
        return False


def _literal_match(haystack: str, needle: str) -> bool:
    return needle.casefold() in haystack.casefold()


def _hub_id(session_id: str, worldline: str, identity_mode: str) -> str:
    return f"hub:continuity:{session_id}:{worldline}:{identity_mode}"


def parse_projection_request(params: dict[str, list[str]]) -> dict[str, Any]:
    unknown = sorted(key for key in params if key not in ALLOWED_QUERY_KEYS)
    if unknown:
        raise ProjectionError(
            "invalid_projection_parameter",
            f"unknown parameter: {unknown[0]}",
        )

    def first(name: str) -> str | None:
        values = params.get(name) or []
        return values[0] if values else None

    session_raw = first("session_id")
    if session_raw is None or session_raw.strip() == "":
        raise ProjectionError("empty_session_id", "session_id is required")
    session_id = session_raw.strip()

    worldline = first("worldline")
    if worldline is None or worldline.strip() == "":
        raise ProjectionError("missing_worldline", "worldline is required")
    if worldline not in ALLOWED_WORLDLINES:
        raise ProjectionError("invalid_worldline", "invalid worldline")

    identity_mode = first("identity_mode")
    if identity_mode is None or identity_mode.strip() == "":
        raise ProjectionError("missing_identity_mode", "identity_mode is required")
    if identity_mode not in ALLOWED_IDENTITY_MODES:
        raise ProjectionError("invalid_identity_mode", "invalid identity_mode")

    view = first("view")
    if view is None or view.strip() == "":
        raise ProjectionError("missing_view", "view is required")
    if view not in ALLOWED_VIEWS:
        raise ProjectionError("invalid_view", "invalid view")
    if view == "local":
        raise ProjectionError(
            "projection_view_unavailable",
            "view=local is not available in S4-MEMORY-SPINE",
        )

    for local_only in ("anchor_type", "anchor_id", "depth"):
        if first(local_only) is not None:
            raise ProjectionError(
                "invalid_projection_parameter",
                f"{local_only} is only valid with view=local",
            )

    query_raw = first("query")
    query = None if query_raw is None else query_raw.strip()
    if query == "":
        query = None
    if query is not None and len(query) > 200:
        raise ProjectionError("query_too_long", "query exceeds 200 characters")

    kinds: list[str] = []
    for value in params.get("kind") or []:
        if value not in ALLOWED_KINDS:
            raise ProjectionError("invalid_kind", f"invalid kind: {value}")
        if value not in kinds:
            kinds.append(value)
    kinds.sort()

    topic_id_raw = first("topic_id")
    topic_id = None if topic_id_raw is None else topic_id_raw.strip() or None

    pinned_raw = first("pinned_only")
    if pinned_raw is None or pinned_raw == "":
        pinned_only = False
    elif pinned_raw == "true":
        pinned_only = True
    elif pinned_raw == "false":
        pinned_only = False
    else:
        raise ProjectionError("invalid_pinned_only", "pinned_only must be true or false")

    from_raw = first("updated_from")
    to_raw = first("updated_to")
    updated_from = None
    updated_to = None
    if from_raw is not None and from_raw.strip() != "":
        updated_from = _parse_rfc3339(from_raw, "updated_from")
    if to_raw is not None and to_raw.strip() != "":
        updated_to = _parse_rfc3339(to_raw, "updated_to")
    if updated_from is not None and updated_to is not None and updated_from > updated_to:
        raise ProjectionError("invalid_time_range", "updated_from is after updated_to")

    return {
        "session_id": session_id,
        "worldline": worldline,
        "identity_mode": identity_mode,
        "view": "overview",
        "query": query,
        "kinds": kinds,
        "topic_id": topic_id,
        "pinned_only": pinned_only,
        "updated_from": updated_from,
        "updated_to": updated_to,
    }


def _evidence_matches(
    *,
    kind: str,
    label: str,
    topic_id: str | None,
    is_pinned: bool,
    updated_at: datetime,
    criteria: dict[str, Any],
) -> bool:
    if criteria["kinds"] and kind not in criteria["kinds"]:
        # kind filter applies to *primary* match; evidence of other kinds
        # may still be admitted later for connectivity. Matching here is
        # primary-match only.
        pass
    if criteria["pinned_only"] and not is_pinned:
        return False
    if criteria["topic_id"] and topic_id != criteria["topic_id"]:
        return False
    if criteria["updated_from"] is not None and updated_at < criteria["updated_from"]:
        return False
    if criteria["updated_to"] is not None and updated_at > criteria["updated_to"]:
        return False
    if criteria["query"] is not None and not _literal_match(label, criteria["query"]):
        return False
    if criteria["kinds"] and kind not in criteria["kinds"]:
        return False
    return True


def _topic_matches(label: str, criteria: dict[str, Any]) -> bool:
    if criteria["kinds"] and "topic" not in criteria["kinds"]:
        return False
    if criteria["pinned_only"]:
        return False
    if criteria["query"] is not None and not _literal_match(label, criteria["query"]):
        return False
    return True


def _topics_are_primary(criteria: dict[str, Any]) -> bool:
    """Topics are primary matches under kind=topic or an active text query."""
    kinds = criteria["kinds"]
    if kinds:
        return "topic" in kinds
    return criteria["query"] is not None


async def project_overview(criteria: dict[str, Any]) -> dict[str, Any]:
    session_id = criteria["session_id"]
    worldline = criteria["worldline"]
    identity_mode = criteria["identity_mode"]
    now = datetime.now(timezone.utc)
    generated_at = _utc_now()
    hub_pid = _hub_id(session_id, worldline, identity_mode)

    try:
        db = await get_db(worldline, "memory")
    except BaseException as exc:
        _raise_storage_unavailable_or_reraise(exc)

    try:
        try:
            await db.execute("BEGIN")
            try:
                fact_rows = await (
                    await db.execute(
                        """SELECT f.fact_id, f.topic_id, f.is_pinned, f.updated_at,
                                  v.display_text
                             FROM stable_facts f
                             JOIN stable_fact_versions v
                               ON v.fact_id = f.fact_id
                              AND v.version_no = f.active_version
                              AND v.invalid_at IS NULL
                            WHERE f.session_id=? AND f.identity_mode=? AND f.state='active'
                              AND NOT EXISTS (
                                    SELECT 1 FROM memory_tombstones t
                                     WHERE t.fact_id = f.fact_id
                                       AND t.session_id = f.session_id
                                       AND t.identity_mode = f.identity_mode
                              )""",
                        (session_id, identity_mode),
                    )
                ).fetchall()
                exp_columns = {
                    str(row[1])
                    for row in await (await db.execute("PRAGMA table_info(experiences)")).fetchall()
                }
                pin_sql = "e.is_pinned" if "is_pinned" in exp_columns else "0 AS is_pinned"
                exp_rows = await (
                    await db.execute(
                        f"""SELECT e.experience_id, e.conversation_id, e.display_text,
                                   e.status, e.expires_at, e.created_at, e.updated_at,
                                   {pin_sql}
                              FROM experiences e
                             WHERE e.session_id=? AND e.identity_mode=?
                               AND e.status='active'""",
                        (session_id, identity_mode),
                    )
                ).fetchall()
                topic_rows = await (
                    await db.execute(
                        """SELECT topic_id, display_label
                             FROM memory_topics
                            WHERE session_id=? AND identity_mode=?""",
                        (session_id, identity_mode),
                    )
                ).fetchall()
            except BaseException:
                try:
                    await db.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            else:
                await db.execute("COMMIT")
        except BaseException as exc:
            _raise_storage_unavailable_or_reraise(exc)
    finally:
        await db.close()

    topics = {
        str(row["topic_id"]): str(row["display_label"] or "")
        for row in topic_rows
    }

    facts: list[dict[str, Any]] = []
    for row in fact_rows:
        updated = _parse_row_time(row["updated_at"])
        facts.append(
            {
                "kind": "fact",
                "id": str(row["fact_id"]),
                "projection_id": f"fact:{row['fact_id']}",
                "label": str(row["display_text"] or ""),
                "topic_id": str(row["topic_id"]) if row["topic_id"] else None,
                "is_pinned": bool(row["is_pinned"]),
                "updated_at": updated,
                "updated_at_raw": str(row["updated_at"] or generated_at),
            }
        )

    experiences: list[dict[str, Any]] = []
    for row in exp_rows:
        if _is_expired(row["status"], row["expires_at"], now):
            continue
        updated = _parse_row_time(row["updated_at"])
        experiences.append(
            {
                "kind": "experience",
                "id": str(row["experience_id"]),
                "projection_id": f"experience:{row['experience_id']}",
                "label": str(row["display_text"] or ""),
                "topic_id": None,
                "is_pinned": bool(row["is_pinned"]),
                "updated_at": updated,
                "updated_at_raw": str(row["updated_at"] or generated_at),
                "created_at": str(row["created_at"] or generated_at),
                "expires_at": row["expires_at"],
                "conversation_id": str(row["conversation_id"] or ""),
            }
        )

    unfiltered_facts = list(facts)
    unfiltered_experiences = list(experiences)
    topic_evidence_count: dict[str, dict[str, int]] = {
        tid: {"fact": 0, "experience": 0} for tid in topics
    }
    for fact in unfiltered_facts:
        tid = fact["topic_id"]
        if tid in topic_evidence_count:
            topic_evidence_count[tid]["fact"] += 1

    composition_topics = [
        tid
        for tid, counts in topic_evidence_count.items()
        if counts["fact"] + counts["experience"] > 0
    ]
    latest_change: datetime | None = None
    for item in unfiltered_facts + unfiltered_experiences:
        if latest_change is None or item["updated_at"] > latest_change:
            latest_change = item["updated_at"]

    matching_facts = [
        item for item in facts if _evidence_matches(kind="fact", label=item["label"], topic_id=item["topic_id"], is_pinned=item["is_pinned"], updated_at=item["updated_at"], criteria=criteria)
    ]
    matching_experiences = [
        item
        for item in experiences
        if _evidence_matches(
            kind="experience",
            label=item["label"],
            topic_id=item["topic_id"],
            is_pinned=item["is_pinned"],
            updated_at=item["updated_at"],
            criteria=criteria,
        )
    ]
    matching_topics = [
        {"id": tid, "label": label, "projection_id": f"topic:{tid}"}
        for tid, label in topics.items()
        if topic_evidence_count[tid]["fact"] + topic_evidence_count[tid]["experience"] > 0
        and _topic_matches(label, criteria)
        and (criteria["topic_id"] is None or tid == criteria["topic_id"])
    ]

    kinds = criteria["kinds"]
    matching_records: list[dict[str, Any]]
    if kinds == ["topic"]:
        primary_matches: list[dict[str, Any]] = list(matching_topics)
        matching_records = []
    elif kinds:
        matching_records = [
            item
            for item in matching_facts + matching_experiences
            if item["kind"] in kinds
        ]
        primary_matches = list(matching_records)
        if "topic" in kinds:
            primary_matches = list(matching_topics) + primary_matches
    elif criteria["query"] is not None:
        primary_matches = list(matching_topics) + matching_facts + matching_experiences
        matching_records = matching_facts + matching_experiences
    else:
        primary_matches = matching_facts + matching_experiences
        matching_records = list(primary_matches)

    eligible_results = len(primary_matches)
    eligible_records = len(matching_records)

    hub_node = {
        "kind": "continuity_hub",
        "projection_id": hub_pid,
        "label_primary": "AMADEUS",
        "label_secondary": "SOUL",
    }

    if eligible_results == 0:
        return {
            "scope": {
                "session_id": session_id,
                "worldline": worldline,
                "identity_mode": identity_mode,
            },
            "view": "overview",
            "projection_version": PROJECTION_VERSION,
            "generated_at": generated_at,
            "criteria": _criteria_echo(criteria),
            "center": {
                "kind": "continuity_hub",
                "projection_id": hub_pid,
                "label_primary": "AMADEUS",
                "label_secondary": "SOUL",
            },
            "composition": {
                "active_facts": len(unfiltered_facts),
                "active_experiences": len(unfiltered_experiences),
                "eligible_topics": len(composition_topics),
                "latest_memory_change_at": (
                    latest_change.isoformat().replace("+00:00", "Z")
                    if latest_change
                    else None
                ),
                "person_anchors_supported": False,
            },
            "budgets": {
                "nodes": OVERVIEW_NODE_BUDGET,
                "edges": OVERVIEW_EDGE_BUDGET,
                "hard_max_nodes": HARD_MAX_NODES,
                "hard_max_edges": HARD_MAX_EDGES,
            },
            "eligible": {
                "nodes": 1,
                "edges": 0,
                "records": 0,
                "results": 0,
            },
            "shown": {"nodes": 1, "edges": 0, "records": 0, "results": 0},
            "truncated": {
                "nodes": False,
                "edges": False,
                "records": False,
                "results": False,
            },
            "empty": True,
            "result_ids": [],
            "nodes": [hub_node],
            "edges": [],
        }

    def evidence_rank_key(item: dict[str, Any]) -> tuple[int, float, str]:
        # pinned first (0), then recency DESC, natural id ASC
        stamp = item["updated_at"].timestamp() if item["updated_at"].tzinfo else item["updated_at"].replace(tzinfo=timezone.utc).timestamp()
        return (0 if item["is_pinned"] else 1, -stamp, item["id"])

    candidate_evidence = matching_facts + matching_experiences
    if _topics_are_primary(criteria):
        # Admit one supporting fact per matched topic so anchors are not orphans.
        used_topics = {
            str(item["topic_id"])
            for item in candidate_evidence
            if item.get("topic_id")
        }
        support: list[dict[str, Any]] = []
        matched_topic_ids = {t["id"] for t in matching_topics}
        for fact in sorted(unfiltered_facts, key=evidence_rank_key):
            tid = fact["topic_id"]
            if tid and tid in matched_topic_ids and tid not in used_topics:
                support.append(fact)
                used_topics.add(tid)
        candidate_evidence = candidate_evidence + support

    remaining_nodes = OVERVIEW_NODE_BUDGET - 1
    remaining_edges = OVERVIEW_EDGE_BUDGET
    shown_topics: dict[str, dict[str, Any]] = {}
    shown_evidence: list[dict[str, Any]] = []
    shown_ids: set[str] = set()

    def can_admit(item: dict[str, Any]) -> bool:
        node_cost = 1
        edge_cost = 1
        tid = item["topic_id"]
        if tid and tid in topics and tid not in shown_topics:
            node_cost += 1
            edge_cost += 1  # hub_to_anchor
        return node_cost <= remaining_nodes and edge_cost <= remaining_edges

    def admit(item: dict[str, Any]) -> None:
        nonlocal remaining_nodes, remaining_edges
        remaining_nodes -= 1
        remaining_edges -= 1
        tid = item["topic_id"]
        if tid and tid in topics and tid not in shown_topics:
            shown_topics[tid] = {
                "kind": "topic",
                "projection_id": f"topic:{tid}",
                "topic_id": tid,
                "label": topics[tid],
                "fact_count": topic_evidence_count[tid]["fact"],
                "experience_count": topic_evidence_count[tid]["experience"],
            }
            remaining_nodes -= 1
            remaining_edges -= 1
        shown_evidence.append(item)
        shown_ids.add(item["projection_id"])

    def try_admit(item: dict[str, Any]) -> bool:
        if item["projection_id"] in shown_ids:
            return False
        if not can_admit(item):
            return False
        admit(item)
        return True

    ranked = sorted(candidate_evidence, key=evidence_rank_key)
    for item in ranked:
        if item["is_pinned"]:
            try_admit(item)

    support_by_topic: dict[str, list[dict[str, Any]]] = {}
    for item in ranked:
        tid = item.get("topic_id")
        if tid and tid in topics:
            support_by_topic.setdefault(str(tid), []).append(item)
    coverage_topics = [
        topic
        for topic in matching_topics
        if topic["id"] in support_by_topic
    ]
    coverage_topics.sort(key=lambda topic: (topic["label"], topic["id"]))
    for topic in coverage_topics:
        if topic["id"] in shown_topics:
            continue
        support = next(
            (
                item
                for item in support_by_topic[topic["id"]]
                if item["projection_id"] not in shown_ids
            ),
            None,
        )
        if support is not None:
            try_admit(support)

    for item in ranked:
        try_admit(item)

    if _topics_are_primary(criteria):
        for topic in sorted(matching_topics, key=lambda t: (t["label"], t["id"])):
            if topic["id"] in shown_topics:
                continue
            support_item = next(
                (
                    fact
                    for fact in sorted(unfiltered_facts, key=evidence_rank_key)
                    if fact["topic_id"] == topic["id"]
                    and fact["projection_id"] not in {e["projection_id"] for e in shown_evidence}
                ),
                None,
            )
            if support_item and can_admit(support_item):
                admit(support_item)

    nodes: list[dict[str, Any]] = [hub_node]
    for topic in shown_topics.values():
        nodes.append(topic)
    for item in shown_evidence:
        if item["kind"] == "fact":
            nodes.append(
                {
                    "kind": "fact",
                    "projection_id": item["projection_id"],
                    "fact_id": item["id"],
                    "label": item["label"],
                    "is_pinned": item["is_pinned"],
                    "updated_at": item["updated_at_raw"],
                    "topic_id": item["topic_id"],
                }
            )
        else:
            nodes.append(
                {
                    "kind": "experience",
                    "projection_id": item["projection_id"],
                    "experience_id": item["id"],
                    "label": item["label"],
                    "is_pinned": item["is_pinned"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at_raw"],
                    "expires_at": item["expires_at"],
                    "is_expired": False,
                    "conversation_id": item["conversation_id"],
                }
            )

    edges: list[dict[str, Any]] = []
    for tid, topic in shown_topics.items():
        edges.append(
            {
                "kind": "hub_to_anchor",
                "from": hub_pid,
                "to": topic["projection_id"],
            }
        )
    for item in shown_evidence:
        tid = item["topic_id"]
        if tid and tid in shown_topics:
            edges.append(
                {
                    "kind": "has_topic",
                    "from": item["projection_id"],
                    "to": f"topic:{tid}",
                }
            )
        else:
            edges.append(
                {
                    "kind": "hub_to_evidence",
                    "from": hub_pid,
                    "to": item["projection_id"],
                }
            )

    nodes.sort(key=lambda n: (KIND_NODE_RANK[n["kind"]], n["projection_id"]))
    edges.sort(key=lambda e: (KIND_EDGE_RANK[e["kind"]], e["from"], e["to"]))

    matched_topic_ids = {t["id"] for t in matching_topics}
    if kinds == ["topic"]:
        shown_primary = [
            n for n in nodes if n["kind"] == "topic" and n["topic_id"] in matched_topic_ids
        ]
        result_rank = sorted(shown_primary, key=lambda n: (n["label"], n["projection_id"]))
        shown_records = 0
    elif _topics_are_primary(criteria):
        topic_results = [
            n for n in nodes if n["kind"] == "topic" and n["topic_id"] in matched_topic_ids
        ]
        topic_results.sort(key=lambda n: (n["label"], n["projection_id"]))
        primary_evidence_ids = {item["projection_id"] for item in matching_records}
        primary_evidence_results = sorted(
            [item for item in shown_evidence if item["projection_id"] in primary_evidence_ids],
            key=evidence_rank_key,
        )
        # Genuine primary evidence first, then matching Topics — not KIND_NODE_RANK.
        # Support-only facts admitted for backbone stay out of result_ids.
        result_rank = (
            [{"projection_id": item["projection_id"]} for item in primary_evidence_results]
            + [{"projection_id": node["projection_id"]} for node in topic_results]
        )
        shown_records = len(primary_evidence_results)
    elif not kinds:
        shown_primary = [n for n in nodes if n["kind"] in {"fact", "experience"}]
        result_rank = sorted(
            shown_evidence,
            key=evidence_rank_key,
        )
        result_rank = [{"projection_id": item["projection_id"]} for item in result_rank]
        shown_records = len(shown_primary)
    else:
        shown_primary = [n for n in nodes if n["kind"] in kinds]
        result_rank = [
            {"projection_id": item["projection_id"]}
            for item in sorted(shown_evidence, key=evidence_rank_key)
            if item["kind"] in kinds
        ]
        shown_records = sum(1 for n in nodes if n["kind"] in {"fact", "experience"} and n["kind"] in kinds)

    result_ids = [item["projection_id"] for item in result_rank]
    shown_results = len(result_ids)

    eligible_nodes = 1 + len(
        {
            t["id"]
            for t in matching_topics
        }
        | (
            set()
            if kinds == ["topic"]
            else {i["id"] for i in matching_facts + matching_experiences}
        )
    )
    if kinds == ["topic"]:
        eligible_nodes = 1 + len(matching_topics) + min(len(matching_topics), len(unfiltered_facts))
    elif _topics_are_primary(criteria):
        primary_topic_ids = {t["id"] for t in matching_topics}
        structural_topics = {
            fact["topic_id"]
            for fact in matching_facts
            if fact["topic_id"] and fact["topic_id"] not in primary_topic_ids
        }
        topics_with_primary_fact = {
            fact["topic_id"] for fact in matching_facts if fact["topic_id"]
        }
        structural_supports = 0
        for topic in matching_topics:
            if topic["id"] in topics_with_primary_fact:
                continue
            if any(fact.get("topic_id") == topic["id"] for fact in unfiltered_facts):
                structural_supports += 1
        eligible_nodes = (
            1
            + len(primary_topic_ids)
            + len(structural_topics)
            + len(matching_facts)
            + len(matching_experiences)
            + structural_supports
        )
    else:
        eligible_topic_ids = {f["topic_id"] for f in matching_facts if f["topic_id"]}
        eligible_nodes = 1 + len(eligible_topic_ids) + len(matching_facts) + len(matching_experiences)

    eligible_edges = max(0, eligible_nodes - 1)

    return {
        "scope": {
            "session_id": session_id,
            "worldline": worldline,
            "identity_mode": identity_mode,
        },
        "view": "overview",
        "projection_version": PROJECTION_VERSION,
        "generated_at": generated_at,
        "criteria": _criteria_echo(criteria),
        "center": {
            "kind": "continuity_hub",
            "projection_id": hub_pid,
            "label_primary": "AMADEUS",
            "label_secondary": "SOUL",
        },
        "composition": {
            "active_facts": len(unfiltered_facts),
            "active_experiences": len(unfiltered_experiences),
            "eligible_topics": len(composition_topics),
            "latest_memory_change_at": (
                latest_change.isoformat().replace("+00:00", "Z")
                if latest_change
                else None
            ),
            "person_anchors_supported": False,
        },
        "budgets": {
            "nodes": OVERVIEW_NODE_BUDGET,
            "edges": OVERVIEW_EDGE_BUDGET,
            "hard_max_nodes": HARD_MAX_NODES,
            "hard_max_edges": HARD_MAX_EDGES,
        },
        "eligible": {
            "nodes": eligible_nodes,
            "edges": eligible_edges,
            "records": eligible_records,
            "results": eligible_results,
        },
        "shown": {
            "nodes": len(nodes),
            "edges": len(edges),
            "records": shown_records,
            "results": shown_results,
        },
        "truncated": {
            "nodes": eligible_nodes > len(nodes),
            "edges": eligible_edges > len(edges),
            "records": eligible_records > shown_records,
            "results": eligible_results > shown_results,
        },
        "empty": False,
        "result_ids": result_ids,
        "nodes": nodes,
        "edges": edges,
    }


def _criteria_echo(criteria: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": criteria["query"],
        "kinds": list(criteria["kinds"]),
        "topic_id": criteria["topic_id"],
        "pinned_only": criteria["pinned_only"],
        "updated_from": (
            criteria["updated_from"].isoformat().replace("+00:00", "Z")
            if criteria["updated_from"]
            else None
        ),
        "updated_to": (
            criteria["updated_to"].isoformat().replace("+00:00", "Z")
            if criteria["updated_to"]
            else None
        ),
    }
