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
    ) -> None:
        # Quiet Memory: automatic extraction stays worldline-local; the shared
        # store (Q18) only accepts the explicit upsert_shared_user_fact path.
        mode = normalize_identity_mode(identity_mode)
        for episode in extraction.episodic:
            await self.add_episode(session_id, worldline, episode, identity_mode=mode)
        for fact in extraction.core_facts:
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
    ) -> None:
        # Test/demo tokens must never spawn a real background provider request.
        if not api_key or api_key.lower().startswith("test") or len(api_key) < 8 or not user_text.strip() or not assistant_text.strip():
            return
        provider_snapshot.require(ProviderTask.MEMORY)
        mode = normalize_identity_mode(identity_mode)
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
                session_id, worldline, extraction, identity_mode=mode
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
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db("steins_gate", "control")
        try:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """SELECT id FROM shared_user_facts
                    WHERE owner_session_id=? AND fact_key=? AND is_current=1""",
                (owner_session_id, candidate.fact_key),
            )
            old = await cur.fetchone()
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
                    mode,
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
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()
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
                    WHERE session_id=? AND identity_mode=? AND is_current=1""",
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
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:limit]

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
