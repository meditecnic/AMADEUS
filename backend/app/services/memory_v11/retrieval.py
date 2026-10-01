"""Same-scope stable-fact candidate retrieval for Memory v11 (S3b).

Read-only selection surface used by prompt compilation. Does not write
embeddings, does not merge legacy/shared rows, and never crosses
worldline or identity_mode boundaries.
"""

from __future__ import annotations

import json
import logging
import re
import struct
import unicodedata
from datetime import datetime, timezone
from functools import cmp_to_key
from typing import Any, Protocol

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.memory_v11.contracts import (
    D18_UNRESOLVED_CONTEXT_CAP,
    D18_UNRESOLVED_POOL_LIMIT,
    D18_UNRESOLVED_TOKEN_BUDGET,
)
from app.services.memory_v11.repository import list_active_stable_facts

logger = logging.getLogger(__name__)

# Match PromptCompiler.BUDGETS["core"] so selection fits without mid-fact char slice.
DEFAULT_CANDIDATE_LIMIT: int = 24
CORE_TOKEN_BUDGET: int = 1200
# Conservative overhead for "CORE FACTS" + FACT_USE_CONTRACT header lines.
CONTRACT_OVERHEAD_TOKENS: int = 420
# Pin priority contribution (same scale as legacy v11 pin boost).
PIN_SCORE: float = 2.0

# S3C-2: experiences use their own bounds (PromptCompiler.BUDGETS["episodic"]=1600).
EXPERIENCE_CANDIDATE_LIMIT: int = 16
EXPERIENCE_TOKEN_BUDGET: int = 1400
# Overhead for "EPISODIC MEMORY" + EXPERIENCE_USE_CONTRACT header lines.
EXPERIENCE_CONTRACT_OVERHEAD_TOKENS: int = 180


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
    contract_overhead_tokens: int = CONTRACT_OVERHEAD_TOKENS,
) -> list[dict[str, Any]]:
    """Greedy whole-fact pack under count + token budget; never slice display_text."""
    remaining = max(0, token_budget - contract_overhead_tokens)
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
    """High-recall same-scope rows for background reconciliation.

    Chat PromptCompiler and recall_memory must not call this. Background jobs
    keep allowlist / version / idempotent semantics via
    ``select_reconciliation_candidates``.

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

# ---------------------------------------------------------------------------
# S3C-2: first-class experience retrieval (plan §10.2 / §10.3).
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _cjk_chars(text: str) -> list[str]:
    return [ch for ch in text if unicodedata.category(ch).startswith("Lo")]


def _meaningful_cjk_evidence(display_text: str, query: str) -> bool:
    """Eligibility gate: a single shared bigram (e.g. 昨天) is NOT evidence.

    Eligible when any of:
    1. exact normalized substring match (query inside display);
    2. at least one matched ASCII word token;
    3. at least 2 DISTINCT matched CJK bigrams;
    4. at least 4 distinct matched CJK query characters with >= 0.5 coverage.

    The continuous ranking score stays in _lexical_relevance; this gate only
    decides whether an experience may enter at all.
    """
    query_norm = _normalized_text(query)
    display_norm = _normalized_text(display_text)
    if not query_norm or not display_norm:
        return False
    if query_norm in display_norm:
        return True

    words_q = [w for w in re.findall(r"[0-9a-z]+", query_norm) if len(w) > 1]
    words_h = {w for w in re.findall(r"[0-9a-z]+", display_norm)}
    if words_q and any(w in words_h for w in words_q):
        return True

    cjk_q = _cjk_chars(query_norm)
    cjk_h = _cjk_chars(display_norm)
    bigrams_q = [a + b for a, b in zip(cjk_q, cjk_q[1:])]
    bigrams_h = {a + b for a, b in zip(cjk_h, cjk_h[1:])}
    matched_bigrams = {b for b in bigrams_q if b in bigrams_h}
    if len(matched_bigrams) >= 2:
        return True

    matched_chars = {ch for ch in cjk_q if ch in display_norm}
    return (
        len(matched_chars) >= 4
        and (len(matched_chars) / len(cjk_q)) >= 0.5
    )


def _lexical_relevance(display_text: str, query: str) -> float:
    """Deterministic local CJK-aware relevance; no embeddings, no network.

    Exact normalized substring is the strongest signal (1.0). Otherwise the
    score is a weighted mix of CJK bigram overlap (0.6) and ASCII word-token
    overlap (0.4). Very short pure-CJK queries fall back to character overlap
    scaled at 0.5. Anything unrelated scores exactly 0.0.
    """
    query_norm = _normalized_text(query)
    display_norm = _normalized_text(display_text)
    if not query_norm or not display_norm:
        return 0.0
    if query_norm in display_norm:
        return 1.0

    cjk_q = _cjk_chars(query_norm)
    cjk_h = _cjk_chars(display_norm)
    bigrams_q = [a + b for a, b in zip(cjk_q, cjk_q[1:])]
    bigrams_h = {a + b for a, b in zip(cjk_h, cjk_h[1:])}
    words_q = [w for w in re.findall(r"[0-9a-z]+", query_norm) if len(w) > 1]
    words_h = {w for w in re.findall(r"[0-9a-z]+", display_norm)}

    if bigrams_q or words_q:
        score = 0.0
        if bigrams_q:
            hits = sum(1 for b in bigrams_q if b in bigrams_h)
            score += 0.6 * (hits / len(bigrams_q))
        if words_q:
            hits = sum(1 for w in words_q if w in words_h)
            score += 0.4 * (hits / len(words_q))
        return score
    if cjk_q:
        hits = sum(1 for ch in cjk_q if ch in display_norm)
        return 0.5 * (hits / len(cjk_q))
    return 0.0


async def select_experience_candidates(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    query: str,
    limit: int = EXPERIENCE_CANDIDATE_LIMIT,
    token_budget: int = EXPERIENCE_TOKEN_BUDGET,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Read-only same-scope experience selection for prompt injection.

    Eligible rows: session/identity scope, status='active', and either no
    expires_at or an expiry in the future. Expiry filtering is read behavior
    only (no DELETE, no status rewrite, no TTL cleanup). Relevance is a
    deterministic local signal; recency ranks but never grants eligibility.

    Deliberately no fingerprint dedupe: repeated life events are legitimate
    separate experiences; same-source idempotence already lives in S3C-1.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    now_iso = now or _utc_now_iso()

    db = await get_db(wl, "memory")
    try:
        cur = await db.execute(
            """SELECT experience_id, conversation_id, display_text,
                      semantic_fingerprint, confidence, expires_at, created_at
                 FROM experiences
                WHERE session_id=? AND identity_mode=? AND status='active'""",
            (session_id, mode),
        )
        rows = await cur.fetchall()
    finally:
        await db.close()

    eligible: list[dict[str, Any]] = []
    for row in rows:
        expires_at = row["expires_at"]
        if expires_at is not None:
            try:
                if datetime.fromisoformat(expires_at) <= datetime.fromisoformat(now_iso):
                    continue
            except (TypeError, ValueError):
                # Unparseable expiry: fail closed, never inject.
                continue
        display = str(row["display_text"] or "")
        if not _meaningful_cjk_evidence(display, query):
            continue
        relevance = _lexical_relevance(display, query)
        if relevance <= 0.0:
            continue
        eligible.append(
            {
                "experience_id": str(row["experience_id"]),
                "conversation_id": str(row["conversation_id"] or ""),
                "display_text": display,
                "semantic_fingerprint": str(row["semantic_fingerprint"] or ""),
                "confidence": float(row["confidence"] or 0.0),
                "created_at": str(row["created_at"] or ""),
                "expires_at": expires_at,
                "relevance": relevance,
            }
        )

    # Rank: relevance desc, newer created_at desc, confidence desc, id tie-break.
    def _cmp_experience(
        left: dict[str, Any], right: dict[str, Any]
    ) -> int:
        for field in ("relevance", "created_at", "confidence"):
            lv, rv = left[field], right[field]
            if lv == rv:
                continue
            return -1 if lv > rv else 1
        lid, rid = left["experience_id"], right["experience_id"]
        if lid == rid:
            return 0
        return -1 if lid < rid else 1

    ranked = sorted(eligible, key=cmp_to_key(_cmp_experience))
    return _pack_whole_facts(
        ranked,
        limit=limit,
        token_budget=token_budget,
        contract_overhead_tokens=EXPERIENCE_CONTRACT_OVERHEAD_TOKENS,
    )


async def select_unresolved_observations(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    conversation_id: str,
    current_source_message_id: int,
    query: str,
    pool_limit: int = D18_UNRESOLVED_POOL_LIMIT,
    context_cap: int = D18_UNRESOLVED_CONTEXT_CAP,
    token_budget: int = D18_UNRESOLVED_TOKEN_BUDGET,
) -> list[dict[str, Any]]:
    """Bounded recent unresolved-observation context for the SAME completion.

    Eligibility: same worldline/session/identity/conversation; observation
    status='candidate' AND reanalysis_count=0; at least one present source in
    the current conversation with source_message_id < current.

    P0: one observation = one pool slot — the recent pool derives
    MAX(present source_message_id) per observation (GROUP BY), never a raw
    JOIN + LIMIT. source_excerpt comes from the EARLIEST present source
    (original unresolved wording); recency uses the latest source id.

    Ranking uses the existing local lexical relevance as ORDER ONLY: zero
    lexical overlap is still eligible (pronoun resolution has weak overlap).

    Whole-item packing under D18_UNRESOLVED_CONTEXT_CAP + TOKEN_BUDGET; an
    oversized whole item is omitted, never char-sliced.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = (session_id or "").strip()
    if not session:
        return []
    conv = (conversation_id or "").strip()
    current = int(current_source_message_id)

    db = await get_db(wl, "memory")
    try:
        pool_rows = await (
            await db.execute(
                """SELECT o.observation_id, o.display_text,
                          MAX(s.source_message_id) AS latest_source_id
                     FROM memory_observations o
                     JOIN memory_observation_sources s
                       ON s.observation_id = o.observation_id
                    WHERE o.session_id=? AND o.identity_mode=?
                      AND o.status='candidate' AND o.reanalysis_count=0
                      AND s.source_state='present'
                      AND s.conversation_id=?
                      AND s.source_message_id < ?
                    GROUP BY o.observation_id, o.display_text
                    ORDER BY latest_source_id DESC, o.observation_id ASC
                    LIMIT ?""",
                (session, mode, conv, current, int(pool_limit)),
            )
        ).fetchall()
        candidates: list[dict[str, Any]] = []
        for row in pool_rows:
            observation_id = str(row["observation_id"])
            display_text = str(row["display_text"] or "")
            # EARLIEST present source excerpt (original unresolved wording).
            excerpt = None
            src = await (
                await db.execute(
                    """SELECT excerpt FROM memory_observation_sources
                        WHERE observation_id=? AND source_state='present'
                          AND conversation_id=? AND source_message_id < ?
                        ORDER BY source_message_id ASC LIMIT 1""",
                    (observation_id, conv, current),
                )
            ).fetchone()
            if src is not None and src["excerpt"] is not None:
                excerpt = str(src["excerpt"])
            relevance = _lexical_relevance(display_text, query)
            candidates.append(
                {
                    "observation_id": observation_id,
                    "display_text": display_text,
                    "source_excerpt": excerpt,
                    "_latest_source_id": int(row["latest_source_id"]),
                    "_relevance": relevance,
                }
            )
    finally:
        await db.close()

    # Relevance ranks only; zero relevance stays eligible.
    def _cmp_unresolved(
        left: dict[str, Any], right: dict[str, Any]
    ) -> int:
        for field in ("_relevance", "_latest_source_id"):
            lv, rv = left[field], right[field]
            if lv == rv:
                continue
            return -1 if lv > rv else 1
        lid, rid = left["observation_id"], right["observation_id"]
        if lid == rid:
            return 0
        return -1 if lid < rid else 1

    ranked = sorted(candidates, key=cmp_to_key(_cmp_unresolved))

    # Whole-item packing: both bounds independently.
    selected: list[dict[str, Any]] = []
    remaining = int(token_budget)
    for item in ranked:
        if len(selected) >= int(context_cap):
            break
        payload_item = {
            "observation_id": item["observation_id"],
            "display_text": item["display_text"],
            "source_excerpt": item["source_excerpt"],
        }
        cost = _estimate_tokens(
            json.dumps(payload_item, ensure_ascii=False, sort_keys=True)
        ) + 8  # conservative per-item JSON/contract overhead
        if cost > remaining:
            continue  # omit the whole item; never char-slice
        selected.append(payload_item)
        remaining -= cost
    return selected
