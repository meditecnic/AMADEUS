"""Content-free, best-effort accounting at each paid HTTP request boundary."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, closing, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import logging
import sqlite3

from app import db

logger = logging.getLogger(__name__)
_context = ContextVar("model_usage_context", default=(None, None, "background", "unspecified"))


def set_chat_context(worldline: str, conversation_id: str | None) -> None:
    # Called inside the dedicated turn task after its conversation is fixed.
    # Child translation/render tasks inherit this immutable snapshot.
    _context.set((db.normalize_worldline(worldline), conversation_id, "chat", "chat"))


@contextmanager
def usage_scope(*, source: str, worldline=None, conversation_id=None):
    previous = _context.get()
    token = _context.set((
        db.normalize_worldline(worldline) if worldline else previous[0],
        conversation_id if conversation_id is not None else previous[1],
        "background", source,
    ))
    try:
        yield
    finally:
        _context.reset(token)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class Usage:
    def __init__(self, provider_id, model_id):
        self.provider_id, self.model_id = provider_id, model_id
        self.context = _context.get()
        self.time = datetime.now(timezone.utc).isoformat()
        self.input = self.output = self.cached = None
        self.invalid = False

    def observe(self, payload):
        if self.invalid:
            return
        try:
            self._observe(payload)
        except Exception:
            # Provider accounting metadata is untrusted. A malformed report must
            # never interrupt the reply or leave partially parsed token counts.
            self.input = self.output = self.cached = None
            self.invalid = True

    def _observe(self, payload):
        if not isinstance(payload, dict):
            return
        usage = payload.get("usage") or payload.get("usageMetadata")
        if not isinstance(usage, dict):
            return
        # Streaming reports are cumulative: keep the final report, never sum chunks.
        if "promptTokenCount" in usage:
            self.input = _count(usage.get("promptTokenCount"))
            output = _count(usage.get("candidatesTokenCount"))
            thoughts = _count(usage.get("thoughtsTokenCount"))
            self.output = (output or 0) + (thoughts or 0) if output is not None else None
            self.cached = _count(usage.get("cachedContentTokenCount"))
        else:
            self.input = _count(usage.get("prompt_tokens", usage.get("input_tokens")))
            self.output = _count(usage.get("completion_tokens", usage.get("output_tokens")))
            details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
            self.cached = _count(usage.get("prompt_cache_hit_tokens", details.get("cached_tokens")))

    def save(self):
        # No request/response content, URLs, credentials or exception text is retained.
        try:
            with closing(sqlite3.connect(db._resolve_path("steins_gate", "control"), timeout=1)) as conn:
                conn.execute(
                    """INSERT INTO model_usage
                    (time_utc,worldline,conversation_id,category,source,provider_id,model_id,
                     input_tokens,output_tokens,cached_tokens,reported)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (self.time, *self.context, self.provider_id, self.model_id,
                     self.input, self.output, self.cached,
                     int(self.input is not None and self.output is not None)),
                )
                conn.commit()
        except Exception:
            logger.warning("model_usage_write_failed")


@asynccontextmanager
async def usage_call(provider_id: str, model_id: str):
    usage = Usage(provider_id, model_id)
    try:
        yield usage
    finally:
        # Also retain unreported attempts on cancellation/transport/parse failure.
        await asyncio.shield(asyncio.to_thread(usage.save))
