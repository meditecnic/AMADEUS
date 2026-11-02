"""Shared memory embedding adapter and legacy-table erasure.

The v11 memory modules own extraction, retrieval and the archive. This module
keeps the local embedding adapter they share, and the conversation-erasure helper
that still removes rows from the legacy memory tables.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app import config
from app.db import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    get_db,
    normalize_worldline,
)

# Recent-window size. MUST match chat history compression keep_count
# (chat_ws.compress_and_update_history). Do not raise one without the other.
MEMORY_INGEST_WINDOW_N = 6


class RetrievedMemory(BaseModel):
    id: int
    kind: Literal["episodic", "core"]
    content: str
    score: float
    confidence: float = 1.0
    importance: float = 0.5
    pinned: bool = False


EMBEDDING_REVISION = "ac5d898c8d382b17167c33e5c8af644a3519b47d"


class EmbeddingAdapter:
    """Lazy local jina adapter; no model is loaded during import or ordinary tests."""

    def __init__(self, model_name: str = EMBEDDING_MODEL, dimensions: int = EMBEDDING_DIMENSION,
                 model_dir: str | Path | None = None):
        self.model_name = model_name
        self.dimensions = dimensions
        self.model_dir = Path(model_dir) if model_dir is not None else None
        self._model = None
        self._tokenizer = None
        self._load_lock = threading.Lock()

    def _model_file(self, filename: str) -> str:
        source = f"https://huggingface.co/{self.model_name}/resolve/{EMBEDDING_REVISION}/{filename}"
        if self.model_dir is not None:
            path = self.model_dir / filename
            if not path.is_file():
                raise FileNotFoundError(f"Missing embedding file: {path}; download from {source}")
            return str(path)
        from huggingface_hub import hf_hub_download
        try:
            return hf_hub_download(self.model_name, filename, revision=EMBEDDING_REVISION)
        except Exception as exc:
            raise RuntimeError(f"Cannot load embedding file {filename}; download from {source}: {exc}") from exc

    def _load(self):
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    import onnxruntime
                    from tokenizers import Tokenizer

                    model_path = self._model_file("onnx/model.onnx")
                    tokenizer = Tokenizer.from_file(self._model_file("tokenizer.json"))
                    self._model_file("onnx/model.onnx_data")
                    self._tokenizer = tokenizer
                    self._model = onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        return self._model

    async def encode_passage(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._encode, f"Document: {text}")

    async def encode_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._encode, f"Query: {text}")

    def _encode(self, text: str) -> list[float]:
        import numpy as np

        session = self._load()
        # Match embedding_bench.py (c82d0ea): prefix first, no whitespace
        # stripping, tokenize once and slice to the same 512-token limit.
        ids = self._tokenizer.encode(text).ids[:512]
        input_ids = np.array([ids], dtype=np.int64)
        inputs = {"input_ids": input_ids, "attention_mask": np.ones_like(input_ids)}
        for item in session.get_inputs():
            if item.name == "position_ids":
                inputs[item.name] = np.arange(len(ids), dtype=np.int64)[None, :]
            elif item.name == "token_type_ids":
                inputs[item.name] = np.zeros_like(input_ids)
            elif item.name.startswith("past_key_values."):
                inputs[item.name] = np.zeros((1, item.shape[1], 0, item.shape[3]), dtype=np.float32)
        pooled = session.run(["sentence_embedding"], inputs)[0][0].astype(np.float32)
        vector = (pooled / np.linalg.norm(pooled)).tolist()
        if len(vector) != self.dimensions:
            raise RuntimeError(f"embedding dimension mismatch: expected {self.dimensions}, got {len(vector)}")
        return [float(value) for value in vector]

    def readiness(self) -> dict[str, object]:
        try:
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401
            import sqlite_vec  # noqa: F401
            compatible = self.model_name == config.EMBEDDING_MODEL and self.dimensions == config.EMBEDDING_DIMENSION == 768
            return {"ok": compatible, "model": self.model_name, "dimensions": self.dimensions, "loaded": self._model is not None}
        except ImportError as exc:
            return {"ok": False, "degraded": True, "error": str(exc), "model": self.model_name, "dimensions": self.dimensions}


class MemoryService:
    def __init__(self, embedder: EmbeddingAdapter | None = None):
        self.embedder = embedder or EmbeddingAdapter()

    async def close(self) -> None:
        return None

    async def forget_exclusive_sources(
        self,
        session_id: str,
        worldline: str,
        source_message_ids: set[int],
        *,
        identity_mode: str | None = None,
    ) -> dict[str, int]:
        """Delete memories whose complete evidence set belongs to one conversation."""
        if not source_message_ids:
            return {"episodic_deleted": 0, "core_facts_deleted": 0}
        wl = normalize_worldline(worldline)
        db = await get_db(wl, "memory")
        episodic_ids: list[int] = []
        core_ids: list[int] = []
        try:
            await db.execute('BEGIN IMMEDIATE')
            for table, destination in (
                ("episodic_memories", episodic_ids),
                ("core_facts", core_ids),
            ):
                rows = await (
                    await db.execute(
                        f"SELECT id,source_message_ids FROM {table} WHERE session_id=? AND (? IS NULL OR identity_mode=?)",
                        (session_id, identity_mode, identity_mode),
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
            # Keep row and vector erasure atomic; a failed vector delete must
            # not lose the row ids needed to retry the durable request.
            if episodic_ids or core_ids:
                import sqlite_vec
                await db.enable_load_extension(True)
                await db.load_extension(sqlite_vec.loadable_path())
                await db.enable_load_extension(False)
                for kind, vector_table, memory_ids in (
                    ('episodic', 'episodic_vec', episodic_ids), ('core', 'core_vec', core_ids),
                ):
                    for memory_id in memory_ids:
                        await db.execute(f'DELETE FROM {vector_table} WHERE rowid=?', (memory_id,))
                        await db.execute('DELETE FROM memory_embeddings WHERE memory_type=? AND memory_id=?', (kind, memory_id))
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
        return {
            "episodic_deleted": len(episodic_ids),
            "core_facts_deleted": len(core_ids),
        }


memory_service = MemoryService()
