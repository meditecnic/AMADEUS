"""Three-layer memory repositories and Python-layer hybrid retrieval."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import struct
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app import config
from app.db import (
    DEFAULT_IDENTITY_MODE,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    _resolve_path,
    get_db,
    normalize_identity_mode,
    normalize_worldline,
)
from app.services.provider_registry import ProviderSnapshot, ProviderTask

logger = logging.getLogger(__name__)

# GATE7A-NAT: seats reserved for shared profile facts in self-mode core fact
# retrieval, so CJK zero-overlap queries cannot evict the stable user profile.
# Small on purpose — must never crowd out the worldline-local budget.
_SHARED_PROFILE_RESERVED_SEATS = 8

# M2: Memory quality gate thresholds
_CORE_CONFIDENCE_THRESHOLD = 0.7
_CORE_IMPORTANCE_THRESHOLD = 0.65
_MAX_CORE_PER_TURN = 2
_N_TURNS_CORE_THROTTLE = 3  # Extract core every N user turns if no intent detected

# M2: Memory intent keywords (Chinese + Japanese + English)
_MEMORY_INTENT_KEYWORDS = [
    # Chinese
    '记住', '别忘了', '请记住', '记下来', '不要忘记', '别忘记',
    '我是', '我叫', '我的名字', '我喜欢', '我讨厌', '我不喜欢',
    '我的爱好', '我的职业', '我的工作', '我住在', '我来自',
    '年龄', '岁', '生日',
    # Japanese
    '覚えて', '忘れない', '記憶', '好き', '嫌い',
    '名前は', '私の名', '趣味', '職業', '年齢',
    # English
    'remember', "don't forget", 'my name', 'I like', 'I hate',
    'my hobby', 'my job', 'I live', 'I am from',
]


def _has_memory_intent(text: str) -> bool:
    """Check if user text contains memory-intent keywords."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in _MEMORY_INTENT_KEYWORDS)


class EpisodicCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=3, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    source_message_ids: list[int] = Field(min_length=1)


class CoreFactCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_key: str = Field(min_length=2, max_length=120)
    fact_value: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    source_message_ids: list[int] = Field(min_length=1)
    is_pinned: bool = False


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    working_summary: str = Field(default="", max_length=4000)
    episodic: list[EpisodicCandidate] = Field(default_factory=list, max_length=8)
    core_facts: list[CoreFactCandidate] = Field(default_factory=list, max_length=8)


class RetrievedMemory(BaseModel):
    id: int
    kind: Literal["episodic", "core"]
    content: str
    score: float
    confidence: float = 1.0
    importance: float = 0.5
    pinned: bool = False


class SharedFactPromotionError(ValueError):
    """Gate 7A promotion failure with a stable, client-safe error code."""

    CODES = frozenset({
        "fact_not_found",
        "fact_not_promotable",
        "source_evidence_unavailable",
        "identity_scope_violation",
        "stale_fact",
    })

    def __init__(self, code: str) -> None:
        if code not in self.CODES:
            code = "fact_not_promotable"
        self.code = code
        super().__init__(code)


class ReclassifyError(ValueError):
    """Gate 7B reclassify failure with a stable, client-safe error code."""

    CODES = frozenset({
        "fact_not_found",
        "not_okabe_fact",
        "evidence_chain_broken",
        "confirmation_required",
        "fact_dismissed",
    })

    def __init__(self, code: str) -> None:
        if code not in self.CODES:
            code = "fact_not_found"
        self.code = code
        super().__init__(code)


class DismissError(ValueError):
    """M3 soft-dismiss failure with a stable, client-safe error code."""

    CODES = frozenset({
        "fact_not_found",
        "not_okabe_fact",
    })

    def __init__(self, code: str) -> None:
        if code not in self.CODES:
            code = "fact_not_found"
        self.code = code
        super().__init__(code)


class MemoryDeleteError(ValueError):
    """Soft-delete failure for established local/shared memory facts."""

    CODES = frozenset({
        "fact_not_found",
        "wrong_scope",
    })

    def __init__(self, code: str) -> None:
        if code not in self.CODES:
            code = "fact_not_found"
        self.code = code
        super().__init__(code)


def reciprocal_rank_fusion(*rankings: Iterable[int], k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return scores


class EmbeddingAdapter:
    """Lazy local E5 adapter; no model is loaded during import or ordinary tests."""

    def __init__(self, model_name: str = EMBEDDING_MODEL, dimensions: int = EMBEDDING_DIMENSION):
        self.model_name = model_name
        self.dimensions = dimensions
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    async def encode_passage(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._encode, f"passage: {text}")

    async def encode_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._encode, f"query: {text}")

    def _encode(self, text: str) -> list[float]:
        vector = self._load().encode(text, normalize_embeddings=True).tolist()
        if len(vector) != self.dimensions:
            raise RuntimeError(f"embedding dimension mismatch: expected {self.dimensions}, got {len(vector)}")
        return [float(value) for value in vector]

    def readiness(self) -> dict[str, object]:
        try:
            import sentence_transformers  # noqa: F401
            import sqlite_vec  # noqa: F401
            compatible = self.model_name == config.EMBEDDING_MODEL and self.dimensions == config.EMBEDDING_DIMENSION == 384
            return {"ok": compatible, "model": self.model_name, "dimensions": self.dimensions, "loaded": self._model is not None}
        except ImportError as exc:
            return {"ok": False, "degraded": True, "error": str(exc), "model": self.model_name, "dimensions": self.dimensions}


class MemoryService:
    def __init__(self, embedder: EmbeddingAdapter | None = None):
        self.embedder = embedder or EmbeddingAdapter()
        self._extraction_tasks: set[asyncio.Task] = set()

    async def add_episode(
        self,
        session_id: str,
        worldline: str,
        candidate: EpisodicCandidate,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> int:
        wl = normalize_worldline(worldline)
        mode = normalize_identity_mode(identity_mode)
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db(wl, "memory")
        try:
            cursor = await db.execute(
                """INSERT INTO episodic_memories(
                       session_id,identity_mode,content,confidence,importance,source_message_ids,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    session_id,
                    mode,
                    candidate.content,
                    candidate.confidence,
                    candidate.importance,
                    json.dumps(candidate.source_message_ids),
                    now,
                ),
            )
            await db.commit()
            memory_id = int(cursor.lastrowid)
        finally:
            await db.close()
        await self._try_index(wl, "episodic", memory_id, candidate.content)
        return memory_id

    async def upsert_core_fact(
        self,
        session_id: str,
        worldline: str,
        candidate: CoreFactCandidate,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> int:
        wl = normalize_worldline(worldline)
        mode = normalize_identity_mode(identity_mode)
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db(wl, "memory")
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """SELECT id FROM core_facts
                    WHERE session_id=? AND identity_mode=? AND fact_key=? AND is_current=1""",
                (session_id, mode, candidate.fact_key),
            )
            old = await cur.fetchone()
            if old:
                await db.execute("UPDATE core_facts SET is_current=0 WHERE id=?", (old["id"],))
            cursor = await db.execute(
                """INSERT INTO core_facts(
                       session_id,identity_mode,fact_key,fact_value,confidence,importance,
                       is_current,is_pinned,source_message_ids,created_at
                   ) VALUES(?,?,?,?,?,?,1,?,?,?)""",
                (
                    session_id,
                    mode,
                    candidate.fact_key,
                    candidate.fact_value,
                    candidate.confidence,
                    candidate.importance,
                    int(candidate.is_pinned),
                    json.dumps(candidate.source_message_ids),
                    now,
                ),
            )
            new_id = int(cursor.lastrowid)
            if old:
                await db.execute("UPDATE core_facts SET superseded_by=? WHERE id=?", (new_id, old["id"]))
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()
        await self._try_index(wl, "core", new_id, f"{candidate.fact_key}: {candidate.fact_value}")
        return new_id

    async def apply_extraction(
        self,
        session_id: str,
        worldline: str,
        extraction: ExtractionResult,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
        *,
        skip_core: bool = False,  # M2: rhythm control — skip core when not triggered
    ) -> None:
        # Quiet Memory: automatic extraction stays worldline-local; the shared
        # store (Q18) only accepts the explicit upsert_shared_user_fact path.
        mode = normalize_identity_mode(identity_mode)
        for episode in extraction.episodic:
            await self.add_episode(session_id, worldline, episode, identity_mode=mode)
        if skip_core:
            return
        # M2: Quality gate — filter by thresholds, cap at _MAX_CORE_PER_TURN
        # M2: User evidence check — core facts must have at least one
        # valid source_message_id (positive int). Pydantic enforces min_length=1
        # on CoreFactCandidate, but we add a defensive check for robustness.
        qualified_cores = [
            fact for fact in extraction.core_facts
            if fact.confidence >= _CORE_CONFIDENCE_THRESHOLD
            and fact.importance >= _CORE_IMPORTANCE_THRESHOLD
            and fact.source_message_ids  # non-empty (Pydantic min_length=1)
            and all(
                isinstance(mid, int) and not isinstance(mid, bool) and mid > 0
                for mid in fact.source_message_ids
            )
        ]
        for fact in qualified_cores[:_MAX_CORE_PER_TURN]:
            await self.upsert_core_fact(session_id, worldline, fact, identity_mode=mode)

    def schedule_turn_extraction(
        self,
        *,
        session_id: str,
        worldline: str,
        user_text: str,
        assistant_text: str,
        source_message_ids: list[int],
        api_key: str | None,
        provider_snapshot: ProviderSnapshot,
        conversation_id: str | None = None,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
        turns_since_last_core: int = 0,  # M2: rhythm control
        skip_core: bool | None = None,  # M2: caller pre-computed rhythm decision
    ) -> None:
        # Test/demo tokens must never spawn a real background provider request.
        if not api_key or api_key.lower().startswith("test") or len(api_key) < 8 or not user_text.strip() or not assistant_text.strip():
            return
        provider_snapshot.require(ProviderTask.MEMORY)
        mode = normalize_identity_mode(identity_mode)
        # M2: Rhythm control — caller may pre-compute skip_core to avoid race
        if skip_core is None:
            intent_hit = _has_memory_intent(user_text)
            skip_core = not (intent_hit or (turns_since_last_core >= _N_TURNS_CORE_THROTTLE))
        task = asyncio.create_task(
            self._extract_turn(
                session_id,
                worldline,
                user_text,
                assistant_text,
                source_message_ids,
                api_key,
                provider_snapshot,
                conversation_id,
                mode,
                skip_core=skip_core,
            )
        )
        self._extraction_tasks.add(task)
        task.add_done_callback(self._extraction_tasks.discard)

    async def _extract_turn(
        self,
        session_id: str,
        worldline: str,
        user_text: str,
        assistant_text: str,
        source_message_ids: list[int],
        api_key: str,
        provider_snapshot: ProviderSnapshot,
        conversation_id: str | None = None,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
        skip_core: bool = False,  # M2: rhythm control
    ) -> None:
        from app import models

        mode = normalize_identity_mode(identity_mode)
        # Q23: write only into the conversation's identity_mode scope; never reclassify
        # okabe-role first-person statements into a real-user/self profile.
        instruction = f"""Extract memory from one conversation turn and return a JSON object with exactly:
working_summary (dialogue flow/open topics only; no discrete facts), episodic (events/promises/shared experiences), core_facts (only stable facts explicitly stated by the user).
Each episodic item requires content, confidence, importance, source_message_ids.
Each core fact requires fact_key, fact_value, confidence, importance, source_message_ids, is_pinned.
Never infer a core fact. If evidence is ambiguous, omit it. Use the supplied source ids only.
Prefer empty core_facts over uncertain ones. If unsure, return empty core_facts list.
Identity scope for this turn is "{mode}". Persist facts only for this scope.
Do not reclassify the speaker as a different identity. Do not promote role-play (okabe) statements into a real-user profile, and do not merge scopes."""
        messages = [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "identity_mode": mode,
                        "source_message_ids": source_message_ids,
                        "user": user_text,
                        "assistant": assistant_text,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            provider_snapshot.require(ProviderTask.MEMORY)
            payload = await provider_snapshot.adapter.complete_json(
                messages=messages,
                api_key=api_key,
                model=provider_snapshot.model_id,
                temperature=0.1,
            )
            extraction = ExtractionResult.model_validate(payload)
            allowed_sources = set(source_message_ids)
            for candidate in [*extraction.episodic, *extraction.core_facts]:
                if not set(candidate.source_message_ids).issubset(allowed_sources):
                    raise ValueError("memory extractor returned an untraceable source_message_id")
            await self.apply_extraction(
                session_id, worldline, extraction, identity_mode=mode,
                skip_core=skip_core,
            )
            if extraction.working_summary:
                await models.save_memory_summary(
                    session_id,
                    extraction.working_summary,
                    worldline=worldline,
                    conversation_id=conversation_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("memory extraction skipped: %s", exc)

    async def close(self) -> None:
        if self._extraction_tasks:
            for task in self._extraction_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._extraction_tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Q18 foundation: cross-worldline shared stable user facts.
    # shared = the durable local session's real-user profile (control.sqlite3
    # only). Explicit self-mode writes only; okabe is fail-closed both ways.
    # ------------------------------------------------------------------

    async def _verify_shared_provenance(
        self,
        owner_session_id: str,
        origin_worldline: str,
        origin_conversation_id: str,
        origin_identity_mode: str,
        source_message_ids: list[int],
    ) -> None:
        """Fail-closed provenance check against the declared origin history DB.

        Reads only the origin worldline's history store — never a cross-
        worldline fallback. The persisted conversation identity_mode is the
        source of truth: a caller claiming 'self' cannot promote evidence from
        an okabe conversation (Q23). Evidence must be user speech only.
        There is no global transaction across history and control; the
        single-user local TOCTOU window is accepted by contract.
        """
        ids = list(source_message_ids)
        if not ids:
            raise ValueError("shared fact requires source_message_ids evidence")
        if any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in ids):
            raise ValueError("shared fact source_message_ids must be positive integers")
        if len(set(ids)) != len(ids):
            raise ValueError("shared fact source_message_ids must be unique")
        if not origin_conversation_id or not str(origin_conversation_id).strip():
            raise ValueError("shared fact requires origin_conversation_id")
        db = await get_db(origin_worldline, "history")
        try:
            cur = await db.execute(
                "SELECT session_id,identity_mode FROM conversations WHERE id=?",
                (origin_conversation_id,),
            )
            conversation = await cur.fetchone()
            if conversation is None or conversation["session_id"] != owner_session_id:
                raise ValueError(
                    "shared fact origin conversation is not owned by this session"
                )
            # Persisted identity is authoritative; the caller's declaration is
            # only a consistency condition and can never upgrade okabe data.
            try:
                persisted_mode = normalize_identity_mode(conversation["identity_mode"])
            except ValueError as exc:
                raise ValueError(
                    "shared fact origin conversation has an invalid identity_mode"
                ) from exc
            if persisted_mode != "self" or persisted_mode != origin_identity_mode:
                raise ValueError(
                    "shared fact origin conversation is not a persisted self conversation"
                )
            for message_id in ids:
                cur = await db.execute(
                    "SELECT session_id,conversation_id,role FROM messages WHERE id=?",
                    (message_id,),
                )
                message = await cur.fetchone()
                if (
                    message is None
                    or message["conversation_id"] != origin_conversation_id
                    or message["session_id"] != owner_session_id
                ):
                    raise ValueError(
                        f"shared fact source message {message_id} does not belong to the declared origin"
                    )
                # Shared facts are user-owned facts: only the user's own words
                # count as evidence, never assistant/system/tool output.
                if message["role"] != "user":
                    raise ValueError(
                        f"shared fact source message {message_id} is not user speech"
                    )
        finally:
            await db.close()

    @staticmethod
    def _shared_row_matches(old, candidate: CoreFactCandidate, wl: str, cid: str) -> bool:
        """Idempotency comparison for a current shared row (inside the txn)."""
        try:
            old_ids = sorted(int(item) for item in json.loads(old["source_message_ids"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return (
            old["fact_value"] == candidate.fact_value
            and float(old["confidence"]) == float(candidate.confidence)
            and float(old["importance"]) == float(candidate.importance)
            and int(old["is_pinned"]) == int(candidate.is_pinned)
            and old["origin_worldline"] == wl
            and old["origin_conversation_id"] == cid
            and old["origin_identity_mode"] == "self"
            and old_ids == sorted(candidate.source_message_ids)
        )

    async def _write_shared_fact_current(
        self,
        owner_session_id: str,
        candidate: CoreFactCandidate,
        wl: str,
        origin_conversation_id: str,
    ) -> tuple[int, bool]:
        """Shared-store write inside one BEGIN IMMEDIATE transaction.

        The idempotency check runs inside the same transaction (never a
        check-then-write across transactions): a fully identical current row
        is a no-op returning (old_id, False); anything else supersedes and
        returns (new_id, True). Superseded rows are kept for the audit chain.
        """
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db("steins_gate", "control")
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """SELECT * FROM shared_user_facts
                    WHERE owner_session_id=? AND fact_key=? AND is_current=1""",
                (owner_session_id, candidate.fact_key),
            )
            old = await cur.fetchone()
            if old is not None and self._shared_row_matches(
                old, candidate, wl, origin_conversation_id
            ):
                await db.commit()
                return int(old["id"]), False
            if old:
                await db.execute(
                    "UPDATE shared_user_facts SET is_current=0 WHERE id=?", (old["id"],)
                )
            cursor = await db.execute(
                """INSERT INTO shared_user_facts(
                       owner_session_id,fact_key,fact_value,confidence,importance,
                       is_current,is_pinned,origin_worldline,origin_conversation_id,
                       origin_identity_mode,source_message_ids,created_at
                   ) VALUES(?,?,?,?,?,1,?,?,?,?,?,?)""",
                (
                    owner_session_id,
                    candidate.fact_key,
                    candidate.fact_value,
                    candidate.confidence,
                    candidate.importance,
                    int(candidate.is_pinned),
                    wl,
                    origin_conversation_id,
                    "self",
                    json.dumps(candidate.source_message_ids),
                    now,
                ),
            )
            new_id = int(cursor.lastrowid)
            if old:
                await db.execute(
                    "UPDATE shared_user_facts SET superseded_by=? WHERE id=?",
                    (new_id, old["id"]),
                )
            await db.commit()
            return new_id, True
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def upsert_shared_user_fact(
        self,
        owner_session_id: str,
        candidate: CoreFactCandidate,
        *,
        origin_worldline: str,
        origin_conversation_id: str,
        origin_identity_mode: str,
    ) -> int:
        """Explicit shared-store write; supersedes the previous current row.

        Superseded rows are kept (never physically deleted) so the audit
        chain (provenance + superseded_by) stays reconstructable.
        """
        wl = normalize_worldline(origin_worldline)
        mode = normalize_identity_mode(origin_identity_mode)
        if mode != "self":
            raise ValueError(
                "shared user facts accept origin_identity_mode='self' only; okabe is fail-closed"
            )
        # owner_session_id keys the shared profile; a blank owner would create
        # an orphan real-user profile and must fail closed before any DB read.
        if not owner_session_id or not str(owner_session_id).strip():
            raise ValueError("shared user facts require a non-empty owner_session_id")
        await self._verify_shared_provenance(
            owner_session_id, wl, origin_conversation_id, mode, candidate.source_message_ids
        )
        new_id, _ = await self._write_shared_fact_current(
            owner_session_id, candidate, wl, origin_conversation_id
        )
        return new_id

    async def _current_shared_fact_rows(self, owner_session_id: str) -> list:
        db = await get_db("steins_gate", "control")
        try:
            cur = await db.execute(
                "SELECT * FROM shared_user_facts WHERE owner_session_id=? AND is_current=1",
                (owner_session_id,),
            )
            return list(await cur.fetchall())
        finally:
            await db.close()

    # ------------------------------------------------------------------
    # Gate 7A: Memory Ledger listing and explicit local→shared promotion.
    # Promotion is a deterministic DB copy — no LLM, provider, translation
    # or TTS involvement, and the client is never trusted for fact content.
    # ------------------------------------------------------------------

    async def _evaluate_promotion_evidence(
        self,
        owner_session_id: str,
        worldline: str,
        source_message_ids_json: str,
    ) -> tuple[str | None, str | None]:
        """Return (origin_conversation_id, reason); reason None means eligible.

        Mirrors the Q18 evidence rules with stable reason codes only — raw SQL
        or storage details never leak to callers.
        """
        # Q18 type rules verbatim: a non-empty JSON list of actual ints (bool
        # is not an int here), positive and unique. No silent coercion of
        # strings/floats — corrupted persisted data must read as unavailable.
        try:
            raw = json.loads(source_message_ids_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, "source_evidence_unavailable"
        if not isinstance(raw, list) or not raw:
            return None, "source_evidence_unavailable"
        if any(
            not isinstance(item, int) or isinstance(item, bool) or item <= 0
            for item in raw
        ):
            return None, "source_evidence_unavailable"
        ids = list(raw)
        if len(set(ids)) != len(ids):
            return None, "source_evidence_unavailable"
        db = await get_db(worldline, "history")
        try:
            conversation_ids: set[str] = set()
            for message_id in ids:
                cur = await db.execute(
                    "SELECT session_id,conversation_id,role FROM messages WHERE id=?",
                    (message_id,),
                )
                message = await cur.fetchone()
                if message is None or message["session_id"] != owner_session_id:
                    return None, "source_evidence_unavailable"
                if message["role"] != "user":
                    return None, "fact_not_promotable"
                conversation_ids.add(message["conversation_id"])
            if len(conversation_ids) != 1:
                return None, "fact_not_promotable"
            origin_conversation_id = next(iter(conversation_ids))
            cur = await db.execute(
                "SELECT session_id,identity_mode FROM conversations WHERE id=?",
                (origin_conversation_id,),
            )
            conversation = await cur.fetchone()
            if conversation is None or conversation["session_id"] != owner_session_id:
                return None, "source_evidence_unavailable"
            try:
                persisted_mode = normalize_identity_mode(conversation["identity_mode"])
            except ValueError:
                return None, "identity_scope_violation"
            if persisted_mode != "self":
                return None, "identity_scope_violation"
            return origin_conversation_id, None
        finally:
            await db.close()

    async def list_memory_facts_for_ledger(
        self,
        session_id: str,
        worldline: str,
    ) -> dict[str, list[dict[str, object]]]:
        """Minimal display model for the Settings Memory Ledger.

        local = the current worldline's self-scope current facts; shared = the
        owner's whole shared profile. Never okabe facts, never raw message
        text, never source ids or storage details.
        """
        wl = normalize_worldline(worldline)
        db = await get_db(wl, "memory")
        try:
            cur = await db.execute(
                """SELECT * FROM core_facts
                    WHERE session_id=? AND identity_mode='self' AND is_current=1
                      AND COALESCE(is_dismissed, 0)=0
                    ORDER BY is_pinned DESC, importance DESC, id DESC""",
                (session_id,),
            )
            local_rows = await cur.fetchall()
            # Include soft-deleted self facts when judging okabe "already_reclassified".
            # Otherwise deleting a reclassified self copy resurrects the okabe row
            # as a pending candidate (user-visible regression).
            cur = await db.execute(
                """SELECT * FROM core_facts
                    WHERE session_id=? AND identity_mode='self' AND is_current=1
                    ORDER BY is_pinned DESC, importance DESC, id DESC""",
                (session_id,),
            )
            local_rows_for_reclassify_match = await cur.fetchall()
            # Gate 7B: okabe candidate facts for reclassify UI (exclude soft-dismissed).
            cur = await db.execute(
                """SELECT * FROM core_facts
                    WHERE session_id=? AND identity_mode='okabe' AND is_current=1
                      AND COALESCE(is_dismissed, 0)=0
                    ORDER BY importance DESC, id DESC""",
                (session_id,),
            )
            okabe_rows = await cur.fetchall()
        finally:
            await db.close()
        local_items: list[dict[str, object]] = []
        shared_rows = await self._current_shared_fact_rows(session_id)
        shared_by_key = {row["fact_key"]: row for row in shared_rows}
        for row in local_rows:
            origin_conversation_id, reason = await self._evaluate_promotion_evidence(
                session_id, wl, row["source_message_ids"]
            )
            # Display-bug fix: when re-promoting this fact would be an exact
            # no-op (same idempotency comparison as the write path), tell the
            # client so the UI stops offering the share action.
            already_shared = False
            if reason is None and origin_conversation_id is not None:
                existing = shared_by_key.get(row["fact_key"])
                if existing is not None:
                    already_shared = self._shared_row_matches(
                        existing,
                        CoreFactCandidate(
                            fact_key=row["fact_key"],
                            fact_value=row["fact_value"],
                            confidence=row["confidence"],
                            importance=row["importance"],
                            source_message_ids=list(json.loads(row["source_message_ids"])),
                            is_pinned=bool(row["is_pinned"]),
                        ),
                        wl,
                        origin_conversation_id,
                    )
            local_items.append({
                "id": row["id"],
                "fact_key": row["fact_key"],
                "fact_value": row["fact_value"],
                "confidence": row["confidence"],
                "importance": row["importance"],
                "is_pinned": bool(row["is_pinned"]),
                "created_at": row["created_at"],
                "promotion_eligible": reason is None,
                "promotion_reason": reason,
                "already_shared": already_shared,
            })
        shared_rows.sort(
            key=lambda row: (-int(row["is_pinned"]), -float(row["importance"]), -int(row["id"]))
        )
        shared_items = [
            {
                "id": row["id"],
                "fact_key": row["fact_key"],
                "fact_value": row["fact_value"],
                "confidence": row["confidence"],
                "importance": row["importance"],
                "is_pinned": bool(row["is_pinned"]),
                "created_at": row["created_at"],
                "origin_worldline": row["origin_worldline"],
            }
            for row in shared_rows
        ]
        # Gate 7B: okabe candidates with already_reclassified flag.
        okabe_items: list[dict[str, object]] = []
        for row in okabe_rows:
            src_ids = self._strict_parse_source_ids(row["source_message_ids"])
            already_reclassified = False
            if src_ids is not None:
                for local_row in local_rows_for_reclassify_match:
                    local_src_ids = self._strict_parse_source_ids(local_row["source_message_ids"])
                    if local_src_ids is None:
                        continue
                    if (
                        local_row["fact_key"] == row["fact_key"]
                        and local_row["fact_value"] == row["fact_value"]
                        and float(local_row["confidence"]) == float(row["confidence"])
                        and float(local_row["importance"]) == float(row["importance"])
                        and int(local_row["is_pinned"]) == int(row["is_pinned"])
                        and local_src_ids == src_ids
                    ):
                        already_reclassified = True
                        break
            okabe_items.append({
                "id": row["id"],
                "fact_key": row["fact_key"],
                "fact_value": row["fact_value"],
                "confidence": row["confidence"],
                "importance": row["importance"],
                "is_pinned": bool(row["is_pinned"]),
                "created_at": row["created_at"],
                "already_reclassified": already_reclassified,
            })
        return {"local": local_items, "shared": shared_items, "okabe": okabe_items}

    async def promote_local_self_fact(
        self,
        session_id: str,
        worldline: str,
        local_fact_id: int,
    ) -> tuple[int, bool]:
        """Explicitly promote one local self core fact into the shared store.

        Everything is re-read from the persistent stores; the caller only
        names the fact. Promotion copies — the local fact is never touched.
        """
        wl = normalize_worldline(worldline)
        if not session_id or not str(session_id).strip():
            raise SharedFactPromotionError("fact_not_found")
        db = await get_db(wl, "memory")
        try:
            cur = await db.execute(
                "SELECT * FROM core_facts WHERE id=?", (int(local_fact_id),)
            )
            row = await cur.fetchone()
        finally:
            await db.close()
        # Ownership failures answer exactly like a missing fact (no existence leak).
        if row is None or row["session_id"] != session_id:
            raise SharedFactPromotionError("fact_not_found")
        try:
            fact_mode = normalize_identity_mode(row["identity_mode"])
        except ValueError as exc:
            raise SharedFactPromotionError("identity_scope_violation") from exc
        if fact_mode != "self":
            raise SharedFactPromotionError("identity_scope_violation")
        if not row["is_current"]:
            raise SharedFactPromotionError("stale_fact")
        if int(row["is_dismissed"] if "is_dismissed" in row.keys() else 0):
            raise SharedFactPromotionError("fact_not_promotable")
        origin_conversation_id, reason = await self._evaluate_promotion_evidence(
            session_id, wl, row["source_message_ids"]
        )
        if reason is not None or origin_conversation_id is None:
            raise SharedFactPromotionError(reason or "fact_not_promotable")
        candidate = CoreFactCandidate(
            fact_key=row["fact_key"],
            fact_value=row["fact_value"],
            confidence=row["confidence"],
            importance=row["importance"],
            # Evidence types were already strictly validated by the evaluate
            # step above; the persisted list is used as-is (no coercion).
            source_message_ids=list(json.loads(row["source_message_ids"])),
            is_pinned=bool(row["is_pinned"]),
        )
        # Final hard gate: the Q18 provenance verification stays authoritative
        # (defense in depth against evaluate/submit TOCTOU drift).
        try:
            await self._verify_shared_provenance(
                session_id, wl, origin_conversation_id, "self", candidate.source_message_ids
            )
        except SharedFactPromotionError:
            raise
        except ValueError as exc:
            raise SharedFactPromotionError("fact_not_promotable") from exc
        return await self._write_shared_fact_current(
            session_id, candidate, wl, origin_conversation_id
        )

    async def reclassify_okabe_fact_to_self(
        self,
        session_id: str,
        worldline: str,
        okabe_fact_id: int,
        *,
        confirmed: bool = False,
    ) -> tuple[int, bool]:
        """Gate 7B: reclassify one okabe-scope core fact into self scope.

        Copy semantics (Q1=A): the okabe fact is retained untouched.
        Irreversible (Q2=A): no reverse path in this version.
        Two-step (Q0=A): writes only to self local core_facts, never shared.
        """
        wl = normalize_worldline(worldline)

        # --- Pre-transaction: explicit user confirmation gate ---
        if not confirmed:
            raise ReclassifyError("confirmation_required")

        # --- Pre-transaction: read okabe fact (memory DB) ---
        db = await get_db(wl, "memory")
        try:
            cur = await db.execute(
                "SELECT * FROM core_facts WHERE id=?", (int(okabe_fact_id),)
            )
            row = await cur.fetchone()
        finally:
            await db.close()

        if row is None or row["session_id"] != session_id:
            raise ReclassifyError("fact_not_found")
        try:
            fact_mode = normalize_identity_mode(row["identity_mode"])
        except ValueError:
            raise ReclassifyError("not_okabe_fact")
        if fact_mode != "okabe":
            raise ReclassifyError("not_okabe_fact")
        if not row["is_current"]:
            raise ReclassifyError("fact_not_found")
        if int(row["is_dismissed"] if "is_dismissed" in row.keys() else 0):
            raise ReclassifyError("fact_dismissed")

        # --- Pre-transaction: evidence chain validation ---
        source_ids = await self._validate_reclassify_evidence(
            session_id, wl, row["source_message_ids"]
        )

        # --- Write transaction: BEGIN IMMEDIATE on memory DB ---
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db(wl, "memory")
        try:
            await db.execute("BEGIN IMMEDIATE")

            # Step 3a: Re-read source fact inside transaction
            cur = await db.execute(
                "SELECT * FROM core_facts WHERE id=?", (int(okabe_fact_id),)
            )
            fresh = await cur.fetchone()
            if (
                fresh is None
                or fresh["session_id"] != session_id
                or normalize_identity_mode(fresh["identity_mode"]) != "okabe"
                or not fresh["is_current"]
            ):
                await db.rollback()
                raise ReclassifyError("fact_not_found")
            if int(fresh["is_dismissed"] if "is_dismissed" in fresh.keys() else 0):
                await db.rollback()
                raise ReclassifyError("fact_dismissed")

            # Step 3b: Exact-match idempotency check
            cur = await db.execute(
                """SELECT * FROM core_facts
                    WHERE session_id=? AND identity_mode='self'
                      AND fact_key=? AND is_current=1
                      AND COALESCE(is_dismissed, 0)=0""",
                (session_id, row["fact_key"]),
            )
            existing = await cur.fetchone()
            if existing is not None and self._reclassify_row_matches(existing, row, source_ids):
                await db.commit()
                return int(existing["id"]), False

            # Step 3c: Supersede existing self fact with same key (if any)
            if existing is not None:
                await db.execute(
                    "UPDATE core_facts SET is_current=0 WHERE id=?",
                    (existing["id"],),
                )

            # Step 3d: INSERT new self fact
            cursor = await db.execute(
                """INSERT INTO core_facts(
                       session_id,identity_mode,fact_key,fact_value,confidence,importance,
                       is_current,is_pinned,source_message_ids,created_at
                   ) VALUES(?,?,?,?,?,?,1,?,?,?)""",
                (
                    session_id,
                    "self",
                    row["fact_key"],
                    row["fact_value"],
                    row["confidence"],
                    row["importance"],
                    int(row["is_pinned"]),
                    row["source_message_ids"],
                    now,
                ),
            )
            new_id = int(cursor.lastrowid)

            # Step 3e: Update superseded_by
            if existing is not None:
                await db.execute(
                    "UPDATE core_facts SET superseded_by=? WHERE id=?",
                    (new_id, existing["id"]),
                )

            await db.commit()
        except ReclassifyError:
            raise
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

        # Post-commit: embedding index (only on changed=True)
        await self._try_index(wl, "core", new_id, f"{row['fact_key']}: {row['fact_value']}")

        return new_id, True

    async def dismiss_okabe_fact(
        self,
        session_id: str,
        worldline: str,
        okabe_fact_id: int,
    ) -> tuple[int, bool]:
        """M3: soft-dismiss one okabe candidate (hide from ledger + prompt; keep row)."""
        wl = normalize_worldline(worldline)
        if not session_id or not str(session_id).strip():
            raise DismissError("fact_not_found")
        db = await get_db(wl, "memory")
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT * FROM core_facts WHERE id=?", (int(okabe_fact_id),)
            )
            row = await cur.fetchone()
            if row is None or row["session_id"] != session_id:
                await db.rollback()
                raise DismissError("fact_not_found")
            try:
                mode = normalize_identity_mode(row["identity_mode"])
            except ValueError:
                await db.rollback()
                raise DismissError("not_okabe_fact")
            if mode != "okabe" or not row["is_current"]:
                await db.rollback()
                raise DismissError("not_okabe_fact" if mode != "okabe" else "fact_not_found")
            dismissed = int(row["is_dismissed"] if "is_dismissed" in row.keys() else 0)
            if dismissed:
                await db.commit()
                return int(row["id"]), False
            await db.execute(
                "UPDATE core_facts SET is_dismissed=1 WHERE id=?",
                (int(okabe_fact_id),),
            )
            await db.commit()
            return int(okabe_fact_id), True
        except DismissError:
            raise
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def dismiss_okabe_facts_bulk(
        self,
        session_id: str,
        worldline: str,
        fact_ids: list[int],
    ) -> dict[str, list[int]]:
        """M3: soft-dismiss many okabe candidates in one transaction."""
        wl = normalize_worldline(worldline)
        if not session_id or not str(session_id).strip():
            raise DismissError("fact_not_found")
        ids = [int(x) for x in fact_ids if int(x) > 0]
        dismissed: list[int] = []
        skipped: list[int] = []
        if not ids:
            return {"dismissed": dismissed, "skipped": skipped}
        db = await get_db(wl, "memory")
        try:
            await db.execute("BEGIN IMMEDIATE")
            for fid in ids:
                cur = await db.execute("SELECT * FROM core_facts WHERE id=?", (fid,))
                row = await cur.fetchone()
                if (
                    row is None
                    or row["session_id"] != session_id
                    or not row["is_current"]
                ):
                    skipped.append(fid)
                    continue
                try:
                    mode = normalize_identity_mode(row["identity_mode"])
                except ValueError:
                    skipped.append(fid)
                    continue
                if mode != "okabe":
                    skipped.append(fid)
                    continue
                if int(row["is_dismissed"] if "is_dismissed" in row.keys() else 0):
                    skipped.append(fid)
                    continue
                await db.execute(
                    "UPDATE core_facts SET is_dismissed=1 WHERE id=?", (fid,)
                )
                dismissed.append(fid)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()
        return {"dismissed": dismissed, "skipped": skipped}

    async def delete_local_self_fact(
        self,
        session_id: str,
        worldline: str,
        local_fact_id: int,
    ) -> tuple[int, bool]:
        """Soft-delete one established self-local core fact (ledger + prompt).

        Does not touch shared copies. Audit row retained via is_dismissed=1.
        """
        wl = normalize_worldline(worldline)
        if not session_id or not str(session_id).strip():
            raise MemoryDeleteError("fact_not_found")
        db = await get_db(wl, "memory")
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT * FROM core_facts WHERE id=?", (int(local_fact_id),)
            )
            row = await cur.fetchone()
            if row is None or row["session_id"] != session_id or not row["is_current"]:
                await db.rollback()
                raise MemoryDeleteError("fact_not_found")
            try:
                mode = normalize_identity_mode(row["identity_mode"])
            except ValueError:
                await db.rollback()
                raise MemoryDeleteError("wrong_scope")
            if mode != "self":
                await db.rollback()
                raise MemoryDeleteError("wrong_scope")
            if int(row["is_dismissed"] if "is_dismissed" in row.keys() else 0):
                await db.commit()
                return int(row["id"]), False
            await db.execute(
                "UPDATE core_facts SET is_dismissed=1 WHERE id=?",
                (int(local_fact_id),),
            )
            # If this self fact was a reclassify copy, also soft-dismiss the
            # matching okabe source so it cannot reappear as 待确认 or stay in
            # okabe-mode prompt after the user deleted the real-profile copy.
            try:
                src_ids = sorted(
                    int(x) for x in json.loads(row["source_message_ids"] or "[]")
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                src_ids = []
            if src_ids:
                cur = await db.execute(
                    """SELECT * FROM core_facts
                        WHERE session_id=? AND identity_mode='okabe' AND is_current=1
                          AND COALESCE(is_dismissed, 0)=0
                          AND fact_key=? AND fact_value=?""",
                    (session_id, row["fact_key"], row["fact_value"]),
                )
                for okabe_row in await cur.fetchall():
                    try:
                        okabe_ids = sorted(
                            int(x)
                            for x in json.loads(okabe_row["source_message_ids"] or "[]")
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if (
                        okabe_ids == src_ids
                        and float(okabe_row["confidence"]) == float(row["confidence"])
                        and float(okabe_row["importance"]) == float(row["importance"])
                        and int(okabe_row["is_pinned"]) == int(row["is_pinned"])
                    ):
                        await db.execute(
                            "UPDATE core_facts SET is_dismissed=1 WHERE id=?",
                            (int(okabe_row["id"]),),
                        )
            await db.commit()
            return int(local_fact_id), True
        except MemoryDeleteError:
            raise
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def delete_shared_fact(
        self,
        owner_session_id: str,
        shared_fact_id: int,
    ) -> tuple[int, bool]:
        """Soft-delete one shared profile fact (is_current=0). Local copies untouched."""
        if not owner_session_id or not str(owner_session_id).strip():
            raise MemoryDeleteError("fact_not_found")
        db = await get_db("steins_gate", "control")
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT * FROM shared_user_facts WHERE id=?",
                (int(shared_fact_id),),
            )
            row = await cur.fetchone()
            if row is None or row["owner_session_id"] != owner_session_id:
                await db.rollback()
                raise MemoryDeleteError("fact_not_found")
            if not row["is_current"]:
                await db.commit()
                return int(row["id"]), False
            await db.execute(
                "UPDATE shared_user_facts SET is_current=0 WHERE id=?",
                (int(shared_fact_id),),
            )
            await db.commit()
            return int(shared_fact_id), True
        except MemoryDeleteError:
            raise
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def _validate_reclassify_evidence(
        self,
        session_id: str,
        worldline: str,
        source_message_ids_json: str,
    ) -> list[int]:
        """Validate source_message_ids for Gate 7B reclassify.

        Returns the validated list of positive int IDs on success.
        Raises ReclassifyError("evidence_chain_broken") on any failure.
        """
        ids = self._strict_parse_source_ids(source_message_ids_json)
        if ids is None:
            raise ReclassifyError("evidence_chain_broken")

        db = await get_db(worldline, "history")
        try:
            conversation_ids: set[str] = set()
            for message_id in ids:
                cur = await db.execute(
                    "SELECT session_id,conversation_id,role FROM messages WHERE id=?",
                    (message_id,),
                )
                message = await cur.fetchone()
                if message is None or message["session_id"] != session_id:
                    raise ReclassifyError("evidence_chain_broken")
                if message["role"] != "user":
                    raise ReclassifyError("evidence_chain_broken")
                conversation_ids.add(message["conversation_id"])
            if len(conversation_ids) != 1:
                raise ReclassifyError("evidence_chain_broken")
            origin_conversation_id = next(iter(conversation_ids))
            cur = await db.execute(
                "SELECT session_id,identity_mode FROM conversations WHERE id=?",
                (origin_conversation_id,),
            )
            conversation = await cur.fetchone()
            if conversation is None or conversation["session_id"] != session_id:
                raise ReclassifyError("evidence_chain_broken")
            try:
                persisted_mode = normalize_identity_mode(conversation["identity_mode"])
            except ValueError:
                raise ReclassifyError("evidence_chain_broken")
            if persisted_mode != "okabe":
                raise ReclassifyError("evidence_chain_broken")
        finally:
            await db.close()

        return ids

    @staticmethod
    def _strict_parse_source_ids(raw_json: str) -> list[int] | None:
        """Strict source_message_ids parser: actual positive ints only.

        Returns sorted list on success, None on any type violation.
        Never coerces bool/str/float via int(). Rejects duplicates and empty lists.
        """
        try:
            raw = json.loads(raw_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(raw, list) or not raw:
            return None
        result: list[int] = []
        for item in raw:
            # isinstance(bool, int) is True in Python, so we must reject bools first
            if isinstance(item, bool) or not isinstance(item, int):
                return None
            if item <= 0:
                return None
            result.append(item)
        if len(set(result)) != len(result):
            return None
        return sorted(result)

    @staticmethod
    def _reclassify_row_matches(old, okabe_row, validated_source_ids: list[int]) -> bool:
        """Idempotency comparison for Gate 7B reclassify (inside the txn).

        Uses strict parsing: malformed source ids never match (returns False),
        so a legitimate okabe fact reclassify always goes through changed=True
        even if a malformed self row exists with the same fact_key.
        """
        old_ids = MemoryService._strict_parse_source_ids(old["source_message_ids"])
        if old_ids is None:
            return False
        return (
            old["fact_key"] == okabe_row["fact_key"]
            and old["fact_value"] == okabe_row["fact_value"]
            and float(old["confidence"]) == float(okabe_row["confidence"])
            and float(old["importance"]) == float(okabe_row["importance"])
            and int(old["is_pinned"]) == int(okabe_row["is_pinned"])
            and old_ids == sorted(validated_source_ids)
        )

    async def select_core_facts(
        self,
        session_id: str,
        worldline: str,
        query: str,
        limit: int = 24,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> list[RetrievedMemory]:
        mode = normalize_identity_mode(identity_mode)
        db = await get_db(normalize_worldline(worldline), "memory")
        try:
            cur = await db.execute(
                """SELECT * FROM core_facts
                    WHERE session_id=? AND identity_mode=? AND is_current=1
                      AND COALESCE(is_dismissed, 0)=0""",
                (session_id, mode),
            )
            rows = await cur.fetchall()
        finally:
            await db.close()
        query_terms = {term.lower() for term in query.split() if len(term) > 1}
        now = datetime.now(timezone.utc)

        def _score(row) -> float:
            haystack = f"{row['fact_key']} {row['fact_value']}".lower()
            relevance = sum(term in haystack for term in query_terms) / max(1, len(query_terms))
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            recency = math.exp(-max(0.0, (now - created).total_seconds()) / (365 * 86400))
            return 10.0 if row["is_pinned"] else 0.45 * relevance + 0.25 * row["confidence"] + 0.20 * row["importance"] + 0.10 * recency

        ranked = [
            RetrievedMemory(id=row["id"], kind="core", content=f"{row['fact_key']}: {row['fact_value']}", score=_score(row), confidence=row["confidence"], importance=row["importance"], pinned=bool(row["is_pinned"]))
            for row in rows
        ]
        if mode == "self":
            # Q18: self merges the shared profile; the local worldline value
            # wins per fact_key and the shared duplicate is suppressed. Shared
            # rows use negative control-row ids so merged ids never collide
            # with local memory ids.
            local_keys = {row["fact_key"] for row in rows}
            for row in await self._current_shared_fact_rows(session_id):
                if row["fact_key"] in local_keys:
                    continue
                ranked.append(
                    RetrievedMemory(
                        id=-int(row["id"]),
                        kind="core",
                        content=f"{row['fact_key']}: {row['fact_value']}",
                        score=_score(row),
                        confidence=row["confidence"],
                        importance=row["importance"],
                        pinned=bool(row["is_pinned"]),
                    )
                )
        ordered = sorted(ranked, key=lambda item: item.score, reverse=True)
        if mode != "self":
            return ordered[:limit]
        # GATE7A-NAT: CJK queries often share zero surface tokens with fact
        # keys/values, so relevance collapses to 0 and a flood of unrelated
        # high-importance local facts can evict the shared profile entirely.
        # Reserve a few seats for the top-scored shared entries — the stable
        # user profile must stay visible; ordering semantics stay score-based.
        selected = ordered[:limit]
        selected_ids = {item.id for item in selected}
        reserved = [item for item in ordered if item.id < 0][:_SHARED_PROFILE_RESERVED_SEATS]
        missing = [item for item in reserved if item.id not in selected_ids]
        if missing:
            shared_in = [item for item in selected if item.id < 0]
            non_shared = [item for item in selected if item.id > 0]
            room = max(0, limit - len(shared_in) - len(missing))
            selected = shared_in + missing + non_shared[:room]
            selected.sort(key=lambda item: item.score, reverse=True)
        return selected[:limit]

    async def hybrid_search(
        self,
        session_id: str,
        worldline: str,
        query: str,
        top_k: int = 12,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> list[RetrievedMemory]:
        wl = normalize_worldline(worldline)
        mode = normalize_identity_mode(identity_mode)
        fts_rows = await self._fts_search(session_id, wl, query, 40, identity_mode=mode)
        vector_rows: list[tuple[int, float]] = []
        try:
            query_vector = await self.embedder.encode_query(query)
            vector_rows = await asyncio.to_thread(
                self._vector_search_sync, session_id, wl, query_vector, 40, mode
            )
        except (ImportError, RuntimeError, sqlite3.Error):
            pass
        fused = reciprocal_rank_fusion([row[0] for row in fts_rows], [row[0] for row in vector_rows])
        content = {row[0]: row[1] for row in fts_rows}
        if vector_rows:
            db = await get_db(wl, "memory")
            try:
                ids = [row[0] for row in vector_rows if row[0] not in content]
                for item_id in ids:
                    cur = await db.execute(
                        """SELECT content FROM episodic_memories
                            WHERE id=? AND session_id=? AND identity_mode=?""",
                        (item_id, session_id, mode),
                    )
                    row = await cur.fetchone()
                    if row:
                        content[item_id] = row["content"]
            finally:
                await db.close()
        ordered = sorted(fused.items(), key=lambda pair: pair[1], reverse=True)[:top_k]
        return [RetrievedMemory(id=item_id, kind="episodic", content=content.get(item_id, ""), score=score) for item_id, score in ordered if content.get(item_id)]

    async def lexical_search(
        self,
        session_id: str,
        worldline: str,
        query: str,
        top_k: int = 12,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> list[RetrievedMemory]:
        mode = normalize_identity_mode(identity_mode)
        rows = await self._fts_search(
            session_id,
            normalize_worldline(worldline),
            query,
            min(40, max(1, top_k)),
            identity_mode=mode,
        )
        return [RetrievedMemory(id=item_id, kind="episodic", content=content, score=1.0 / rank) for rank, (item_id, content) in enumerate(rows, 1)]

    async def status(self, session_id: str, worldline: str) -> dict[str, object]:
        # Session-level totals across modes (wipe/status); retrieval remains mode-scoped.
        db = await get_db(normalize_worldline(worldline), "memory")
        try:
            episode = await (await db.execute("SELECT COUNT(*) FROM episodic_memories WHERE session_id=?", (session_id,))).fetchone()
            facts = await (await db.execute("SELECT COUNT(*) FROM core_facts WHERE session_id=? AND is_current=1", (session_id,))).fetchone()
            return {"session_id": session_id, "worldline": normalize_worldline(worldline), "episodic_count": episode[0], "current_fact_count": facts[0], "embedding": self.embedder.readiness()}
        finally:
            await db.close()

    async def forget(self, session_id: str, worldline: str, scope: str = "all") -> None:
        wl = normalize_worldline(worldline)
        db = await get_db(wl, "memory")
        episode_ids: list[int] = []
        core_ids: list[int] = []
        try:
            if scope in ("all", "episodic"):
                episode_ids = [row[0] for row in await (await db.execute("SELECT id FROM episodic_memories WHERE session_id=?", (session_id,))).fetchall()]
                await db.execute("DELETE FROM episodic_memories WHERE session_id=?", (session_id,))
            if scope in ("all", "core"):
                core_ids = [row[0] for row in await (await db.execute("SELECT id FROM core_facts WHERE session_id=?", (session_id,))).fetchall()]
                await db.execute("DELETE FROM core_facts WHERE session_id=?", (session_id,))
            if scope in ("all", "emotion"):
                await db.execute("DELETE FROM emotion_states WHERE session_id=?", (session_id,))
            await db.commit()
        finally:
            await db.close()
        if episode_ids or core_ids:
            await asyncio.to_thread(self._delete_vectors_sync, wl, episode_ids, core_ids)

    async def forget_exclusive_sources(
        self,
        session_id: str,
        worldline: str,
        source_message_ids: set[int],
    ) -> dict[str, int]:
        """Delete memories whose complete evidence set belongs to one conversation."""
        if not source_message_ids:
            return {"episodic_deleted": 0, "core_facts_deleted": 0}
        wl = normalize_worldline(worldline)
        db = await get_db(wl, "memory")
        episodic_ids: list[int] = []
        core_ids: list[int] = []
        try:
            for table, destination in (
                ("episodic_memories", episodic_ids),
                ("core_facts", core_ids),
            ):
                rows = await (
                    await db.execute(
                        f"SELECT id,source_message_ids FROM {table} WHERE session_id=?",
                        (session_id,),
                    )
                ).fetchall()
                for row in rows:
                    try:
                        sources = {
                            int(item) for item in json.loads(row["source_message_ids"])
                        }
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if sources and sources.issubset(source_message_ids):
                        destination.append(int(row["id"]))
            if core_ids:
                placeholders = ",".join("?" for _ in core_ids)
                await db.execute(
                    f"UPDATE core_facts SET superseded_by=NULL WHERE superseded_by IN ({placeholders})",
                    core_ids,
                )
                await db.execute(
                    f"DELETE FROM core_facts WHERE id IN ({placeholders})",
                    core_ids,
                )
            if episodic_ids:
                placeholders = ",".join("?" for _ in episodic_ids)
                await db.execute(
                    f"DELETE FROM episodic_memories WHERE id IN ({placeholders})",
                    episodic_ids,
                )
            await db.commit()
        finally:
            await db.close()
        if episodic_ids or core_ids:
            await asyncio.to_thread(
                self._delete_vectors_sync,
                wl,
                episodic_ids,
                core_ids,
            )
        return {
            "episodic_deleted": len(episodic_ids),
            "core_facts_deleted": len(core_ids),
        }

    async def _fts_search(
        self,
        session_id: str,
        worldline: str,
        query: str,
        limit: int,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> list[tuple[int, str]]:
        if not query.strip():
            return []
        mode = normalize_identity_mode(identity_mode)
        db = await get_db(worldline, "memory")
        try:
            try:
                cur = await db.execute(
                    """SELECT e.id,e.content
                         FROM episodic_fts f
                         JOIN episodic_memories e ON e.id=f.rowid
                        WHERE episodic_fts MATCH ?
                          AND e.session_id=?
                          AND e.identity_mode=?
                        ORDER BY bm25(episodic_fts)
                        LIMIT ?""",
                    (query, session_id, mode, limit),
                )
                return [(row["id"], row["content"]) for row in await cur.fetchall()]
            except sqlite3.OperationalError:
                return []
        finally:
            await db.close()

    async def _try_index(self, worldline: str, kind: str, memory_id: int, text: str) -> None:
        try:
            vector = await self.embedder.encode_passage(text)
            await asyncio.to_thread(self._index_sync, worldline, kind, memory_id, vector)
        except (ImportError, RuntimeError, sqlite3.Error):
            return

    @staticmethod
    def _index_sync(worldline: str, kind: str, memory_id: int, vector: list[float]) -> None:
        import sqlite_vec
        path = _resolve_path(worldline, "memory")
        connection = sqlite3.connect(path)
        try:
            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            blob = struct.pack(f"<{len(vector)}f", *vector)
            table = "episodic_vec" if kind == "episodic" else "core_vec"
            connection.execute(f"INSERT OR REPLACE INTO {table}(rowid,embedding) VALUES(?,?)", (memory_id, blob))
            connection.execute("INSERT OR REPLACE INTO memory_embeddings(memory_type,memory_id,model,dimensions,vector) VALUES(?,?,?,?,?)", (kind, memory_id, EMBEDDING_MODEL, EMBEDDING_DIMENSION, blob))
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _vector_search_sync(
        session_id: str,
        worldline: str,
        vector: list[float],
        limit: int,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> list[tuple[int, float]]:
        import sqlite_vec
        mode = normalize_identity_mode(identity_mode)
        connection = sqlite3.connect(_resolve_path(worldline, "memory"))
        try:
            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            blob = struct.pack(f"<{len(vector)}f", *vector)
            return [(int(row[0]), float(row[1])) for row in connection.execute(
                """SELECT v.rowid,v.distance
                     FROM episodic_vec v
                     JOIN episodic_memories e ON e.id=v.rowid
                    WHERE v.embedding MATCH ?
                      AND k=?
                      AND e.session_id=?
                      AND e.identity_mode=?
                    ORDER BY v.distance""",
                (blob, limit, session_id, mode),
            ).fetchall()]
        finally:
            connection.close()

    @staticmethod
    def _delete_vectors_sync(worldline: str, episode_ids: list[int], core_ids: list[int]) -> None:
        try:
            import sqlite_vec
        except ImportError:
            return
        connection = sqlite3.connect(_resolve_path(worldline, "memory"))
        try:
            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            for memory_id in episode_ids:
                connection.execute("DELETE FROM episodic_vec WHERE rowid=?", (memory_id,))
                connection.execute("DELETE FROM memory_embeddings WHERE memory_type='episodic' AND memory_id=?", (memory_id,))
            for memory_id in core_ids:
                connection.execute("DELETE FROM core_vec WHERE rowid=?", (memory_id,))
                connection.execute("DELETE FROM memory_embeddings WHERE memory_type='core' AND memory_id=?", (memory_id,))
            connection.commit()
        finally:
            connection.close()


memory_service = MemoryService()
