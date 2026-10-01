"""Stable-fact version embedding helpers for Memory v11 (S3b-2).

Passage encoding is intentionaly separate from SQLite write transactions:
callers must prepare vectors before BEGIN IMMEDIATE, then insert BLOBs
inside the same transaction as fact/version rows.
"""

from __future__ import annotations

import math
import struct
from typing import Any, Protocol

from app.db import EMBEDDING_DIMENSION
from app.services.memory_v11.contracts import MemoryValidationError


class PassageEmbedder(Protocol):
    """Existing EmbeddingAdapter surface used for passage indexing."""

    model_name: str
    dimensions: int

    async def encode_passage(self, text: str) -> list[float]: ...


def _resolve_embedder(embedder: PassageEmbedder | None) -> PassageEmbedder:
    if embedder is not None:
        return embedder
    from app.services.memory import memory_service

    return memory_service.embedder


def pack_float32_vector(vector: list[float], *, dimensions: int) -> bytes:
    """Serialize a validated float vector as little-endian float32 BLOB."""
    if dimensions <= 0:
        raise MemoryValidationError(
            "invalid_embedding_dimensions",
            f"dimensions must be positive, got {dimensions}",
        )
    if len(vector) != dimensions:
        raise MemoryValidationError(
            "invalid_embedding_vector",
            f"vector length {len(vector)} != dimensions {dimensions}",
        )
    try:
        floats = [float(x) for x in vector]
    except (TypeError, ValueError) as exc:
        raise MemoryValidationError(
            "invalid_embedding_vector",
            "vector components must be float-compatible",
        ) from exc
    for index, value in enumerate(floats):
        if not math.isfinite(value):
            raise MemoryValidationError(
                "invalid_embedding_vector",
                f"vector component {index} is not finite",
            )
    return struct.pack(f"{dimensions}f", *floats)


async def prepare_passage_embedding(
    text: str,
    *,
    embedder: PassageEmbedder | None = None,
) -> dict[str, Any]:
    """Encode display_text via EmbeddingAdapter and validate for storage.

    Fail-closed: empty model, wrong dimensions, encode errors, or malformed
    vectors raise MemoryValidationError so the caller never opens a write txn
    that would commit a version without an index row.
    """
    resolved = _resolve_embedder(embedder)
    model = str(getattr(resolved, "model_name", "") or "").strip()
    dims = int(getattr(resolved, "dimensions", 0) or 0)
    if not model:
        raise MemoryValidationError(
            "invalid_embedding_model",
            "embedder.model_name is required",
        )
    if dims != int(EMBEDDING_DIMENSION):
        raise MemoryValidationError(
            "invalid_embedding_dimensions",
            f"dimensions must be {EMBEDDING_DIMENSION}, got {dims}",
        )
    passage = (text or "").strip()
    if not passage:
        raise MemoryValidationError(
            "empty_display_text",
            "cannot embed empty display_text",
        )
    try:
        vector = await resolved.encode_passage(passage)
    except MemoryValidationError:
        raise
    except Exception as exc:
        raise MemoryValidationError(
            "embedding_failed",
            f"encode_passage failed: {exc}",
        ) from exc
    if not isinstance(vector, list):
        raise MemoryValidationError(
            "invalid_embedding_vector",
            f"encode_passage must return list, got {type(vector)!r}",
        )
    blob = pack_float32_vector(vector, dimensions=dims)
    return {
        "model": model,
        "dimensions": dims,
        "vector": blob,
    }


async def insert_version_embedding(
    db: Any,
    *,
    fact_id: str,
    version_no: int,
    prepared: dict[str, Any],
) -> None:
    """Insert one stable_fact_version_embeddings row (same transaction as version)."""
    await db.execute(
        """INSERT INTO stable_fact_version_embeddings(
               fact_id, version_no, model, dimensions, vector
           ) VALUES(?,?,?,?,?)""",
        (
            str(fact_id),
            int(version_no),
            str(prepared["model"]),
            int(prepared["dimensions"]),
            prepared["vector"],
        ),
    )


async def delete_version_embedding(
    db: Any,
    *,
    fact_id: str,
    version_no: int,
) -> None:
    """Physically remove an obsolete version vector (no soft-active column)."""
    await db.execute(
        """DELETE FROM stable_fact_version_embeddings
            WHERE fact_id=? AND version_no=?""",
        (str(fact_id), int(version_no)),
    )
