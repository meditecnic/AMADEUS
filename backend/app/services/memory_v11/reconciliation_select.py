"""High-recall candidate allowlist for background Memory completion.

Chat recall and PromptCompiler must not import or call this module.
Jobs keep the existing version/allowlist/idempotent completion contract.
"""

from __future__ import annotations

from typing import Any

from app.services.memory_v11 import retrieval as retrieval_module
from app.services.memory_v11.retrieval import QueryEmbedder


async def select_reconciliation_candidates(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    source_text: str,
    embedder: QueryEmbedder | None = None,
) -> list[dict[str, Any]]:
    """Return same-scope fact rows as a completion allowlist.

    This is not a chat relevance verdict and does not inherit 4/8/1200 or
    no-call semantics.
    """
    return await retrieval_module.select_stable_fact_candidates(
        session_id=session_id,
        worldline=worldline,
        identity_mode=identity_mode,
        query=source_text,
        embedder=embedder,
    )
