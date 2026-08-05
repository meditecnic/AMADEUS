"""Same-scope stable-fact candidate retrieval for Memory v11 (S3b).

Read-only selection surface used by prompt compilation. Does not write
embeddings, does not merge legacy/shared rows, and never crosses
worldline or identity_mode boundaries.
"""

from __future__ import annotations

import logging
import struct
from typing import Any, Protocol

from app.db import normalize_identity_mode, normalize_worldline
from app.services.memory_v11.repository import list_active_stable_facts

logger = logging.getLogger(__name__)

# Match PromptCompiler.BUDGETS["core"] so selection fits without mid-fact char slice.
DEFAULT_CANDIDATE_LIMIT: int = 24
CORE_TOKEN_BUDGET: int = 1200
# Conservative overhead for "CORE FACTS" + FACT_USE_CONTRACT header lines.
CONTRACT_OVERHEAD_TOKENS: int = 420
# Pin priority contribution (same scale as legacy v11 pin boost).
PIN_SCORE: float = 2.0


class QueryEmbedder(Protocol):
    """Existing EmbeddingAdapter surface used for optional vector scoring."""

    model_name: str
    dimensions: int

    async def encode_query(self, text: str) -> list[float]: ...


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 2) // 3)


def _lexical_score(display_text: str, query: str) -> float:
    raw = (query or "").strip()
    terms: set[str] = set()
    if raw:
        terms.add(raw.lower())
        for term in raw.split():
            if len(term) > 1:
                terms.add(term.lower())
    if not terms:
        return 0.0
    haystack = (display_text or "").lower()
    hits = sum(1 for term in terms if term in haystack)
    return hits / len(terms)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    # Vectors from EmbeddingAdapter / test stubs are L2-normalized unit vectors.
    return float(sum(a * b for a, b in zip(left, right)))


def _unpack_vector(blob: bytes, dimensions: int) -> list[float] | None:
    expected = dimensions * 4
    if dimensions <= 0 or len(blob) != expected:
        return None
    try:
        return list(struct.unpack(f"{dimensions}f", blob))
    except struct.error:
        return None


def _resolve_embedder(embedder: QueryEmbedder | None) -> QueryEmbedder | None:
    if embedder is not None:
        return embedder
    try:
        from app.services.memory import memory_service

        return memory_service.embedder
    except Exception:  # pragma: no cover - defensive import/runtime edge
        logger.debug("v11 retrieval: embedder unavailable", exc_info=True)
        return None


def _compatible_vector(
    *,
    model: str,
    dimensions: int,
    vector_blob: bytes,
    embedder: QueryEmbedder,
) -> list[float] | None:
    """Return unpacked vector only when model/dims/blob match the active embedder.

    Fail-closed: mismatched model, wrong dimensions, or malformed BLOB → None.
    No fuzzy model aliasing.
    """
    if str(model) != str(embedder.model_name):
        return None
    expected_dims = int(embedder.dimensions)
    if int(dimensions) != expected_dims:
        return None
    return _unpack_vector(bytes(vector_blob), expected_dims)


async def _load_version_embeddings(
    *,
    worldline: str,
    fact_versions: list[tuple[str, int]],
) -> dict[tuple[str, int], dict[str, Any]]:
    """Load embedding rows (model, dimensions, vector) for fact/version keys."""
    if not fact_versions:
        return {}
    from app.db import get_db

    wl = normalize_worldline(worldline)
    # Deduplicate keys while preserving order for stable parameter binding.
    seen: set[tuple[str, int]] = set()
    keys: list[tuple[str, int]] = []
    for key in fact_versions:
        if key not in seen:
            seen.add(key)
            keys.append(key)

    # Match on (fact_id, version_no) pairs via OR chain — SQLite-friendly.
    clauses = " OR ".join("(fact_id=? AND version_no=?)" for _ in keys)
    params: list[Any] = []
    for fact_id, version_no in keys:
        params.extend([fact_id, version_no])

    db = await get_db(wl, "memory")
    try:
        cur = await db.execute(
            f"""SELECT fact_id, version_no, model, dimensions, vector
                  FROM stable_fact_version_embeddings
                 WHERE {clauses}""",
            params,
        )
        rows = await cur.fetchall()
    finally:
        await db.close()

    out: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        try:
            dims = int(row["dimensions"])
        except (TypeError, ValueError):
            continue
        out[(str(row["fact_id"]), int(row["version_no"]))] = {
            "model": str(row["model"] or ""),
            "dimensions": dims,
            "vector": bytes(row["vector"]),
        }
    return out


def _candidate_score(
    *,
    pinned: bool,
    lexical: float,
    vector: float,
) -> float:
    return (PIN_SCORE if pinned else 0.0) + float(lexical) + float(vector)


def _is_eligible(*, pinned: bool, lexical: float, vector: float) -> bool:
    """Pinned always eligible; otherwise require a positive relevance signal."""
    if pinned:
        return True
    return lexical > 0.0 or vector > 0.0


def _dedupe_by_fingerprint(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one survivor per semantic_fingerprint (retrieval surface only)."""
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in candidates:
        fp = str(item.get("semantic_fingerprint") or "")
        if not fp:
            # Empty fingerprint: treat each fact_id as unique.
            fp = f"fact:{item['fact_id']}"
        prev = best.get(fp)
        if prev is None:
            best[fp] = item
            order.append(fp)
            continue
        # Deterministic survivor: higher score, then confidence, then fact_id.
        prev_key = (
            float(prev["score"]),
            float(prev.get("confidence") or 0.0),
            str(prev["fact_id"]),
        )
        cur_key = (
            float(item["score"]),
            float(item.get("confidence") or 0.0),
            str(item["fact_id"]),
        )
        # Prefer higher score/confidence; on full tie pick lexicographically smaller fact_id.
        if (cur_key[0], cur_key[1], prev_key[2]) > (prev_key[0], prev_key[1], cur_key[2]):
            best[fp] = item
    return [best[fp] for fp in order if fp in best]


def _pack_whole_facts(
    ranked: list[dict[str, Any]],
    *,
    limit: int,
    token_budget: int,
) -> list[dict[str, Any]]:
    """Greedy whole-fact pack under count + token budget; never slice display_text."""
    remaining = max(0, token_budget - CONTRACT_OVERHEAD_TOKENS)
    selected: list[dict[str, Any]] = []
    for item in ranked:
        text = str(item.get("display_text") or "").strip()
        if not text:
            continue
        cost = _estimate_tokens(f"- {text}\n")
        if cost > remaining:
            # Omit oversized facts rather than char-slicing them.
            continue
        selected.append(item)
        remaining -= cost
        if len(selected) >= max(1, limit):
            break
    return selected


async def select_stable_fact_candidates(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    query: str,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    token_budget: int = CORE_TOKEN_BUDGET,
    embedder: QueryEmbedder | None = None,
) -> list[dict[str, Any]]:
    """Select whole active stable facts for prompt injection (same-scope only).

    Signals: pin priority, lexical relevance, optional vector cosine when a
    valid active-version embedding exists, confidence as tie-break.
    Fingerprint dedupe is retrieval-only (no DB writes).
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)

    rows = await list_active_stable_facts(
        session_id=session_id,
        worldline=wl,
        identity_mode=mode,
    )
    if not rows:
        return []

    fact_versions = [
        (str(row["fact_id"]), int(row.get("version_no") or row.get("active_version") or 1))
        for row in rows
    ]
    embedding_rows: dict[tuple[str, int], dict[str, Any]] = {}
    try:
        embedding_rows = await _load_version_embeddings(
            worldline=wl,
            fact_versions=fact_versions,
        )
    except Exception:
        logger.warning(
            "v11 retrieval: embedding table read failed; lexical-only degrade",
            exc_info=True,
        )
        embedding_rows = {}

    # Compatible vectors only: exact model_name match + dimensions + well-formed BLOB.
    resolved = _resolve_embedder(embedder) if embedding_rows else None
    compatible: dict[tuple[str, int], list[float]] = {}
    if resolved is not None:
        for key, payload in embedding_rows.items():
            vector = _compatible_vector(
                model=str(payload.get("model") or ""),
                dimensions=int(payload.get("dimensions") or 0),
                vector_blob=payload.get("vector") or b"",
                embedder=resolved,
            )
            if vector is not None:
                compatible[key] = vector

    query_vector: list[float] | None = None
    if compatible and resolved is not None:
        try:
            query_vector = await resolved.encode_query(query or "")
        except Exception:
            logger.warning(
                "v11 retrieval: query embedding failed; lexical-only degrade",
                exc_info=True,
            )
            query_vector = None

    candidates: list[dict[str, Any]] = []
    for row in rows:
        fact_id = str(row["fact_id"])
        version_no = int(row.get("version_no") or row.get("active_version") or 1)
        display = str(row.get("display_text") or "")
        pinned = bool(row.get("is_pinned"))
        confidence = float(row.get("confidence") or 0.0)
        fingerprint = str(row.get("semantic_fingerprint") or "")
        lexical = _lexical_score(display, query)
        vector = 0.0
        if query_vector is not None:
            stored = compatible.get((fact_id, version_no))
            if stored is not None:
                vector = max(0.0, _cosine(query_vector, stored))

        if not _is_eligible(pinned=pinned, lexical=lexical, vector=vector):
            continue

        score = _candidate_score(pinned=pinned, lexical=lexical, vector=vector)
        candidates.append(
            {
                "fact_id": fact_id,
                "version_no": version_no,
                "display_text": display,
                "is_pinned": pinned,
                "confidence": confidence,
                "semantic_fingerprint": fingerprint,
                "lexical": lexical,
                "vector": vector,
                "score": score,
                "topic_id": row.get("topic_id"),
            }
        )

    if not candidates:
        return []

    deduped = _dedupe_by_fingerprint(candidates)
    # Higher score, higher confidence, then lexicographically smaller fact_id.
    ranked = sorted(
        deduped,
        key=lambda item: (
            -float(item["score"]),
            -float(item.get("confidence") or 0.0),
            str(item["fact_id"]),
        ),
    )
    return _pack_whole_facts(ranked, limit=limit, token_budget=token_budget)
