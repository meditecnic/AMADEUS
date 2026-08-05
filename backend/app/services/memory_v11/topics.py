"""Topic materialization, rename, and listing for Memory v11 (S3D-2).

Deterministic local code only: normalization, dual label/alias resolution,
rename, and scoped listing never call a provider/LLM/network. All write
operations must run inside the caller's already-open memory-DB transaction so
a failed fact batch rolls back any newly created topic with it.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.memory_v11.contracts import MemoryValidationError

_WHITESPACE = re.compile(r"\s+")
# Trailing sentence punctuation ONLY (never leading; ".NET" stays meaningful).
_TRAILING_SENTENCE_PUNCT = "。.!！?？,，、;;；"
_BRACKET_PAIRS = (
    ("《", "》"),
    ("〈", "〉"),
    ("「", "」"),
    ("『", "』"),
    ("【", "】"),
    ("(", ")"),
    ("（", "）"),
    ("[", "]"),
    ("“", "”"),
    ("‘", "’"),
    ('"', '"'),
    ("'", "'"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare_topic_label(raw: Any) -> tuple[str, str] | None:
    """Deterministic label preparation → (display_label, normalized_label).

    Returns None when the input is not usable as a topic suggestion
    (non-string / trims empty). No synonym/NLP logic; internal punctuation
    is preserved.
    """
    if not isinstance(raw, str):
        return None
    display = unicodedata.normalize("NFKC", raw)
    display = display.strip()
    display = _WHITESPACE.sub(" ", display)
    # Peel trailing sentence punctuation and one matching enclosing pair per
    # pass until the result is stable.
    while True:
        before = display
        display = display.rstrip(_TRAILING_SENTENCE_PUNCT)
        display = display.strip()
        for open_c, close_c in _BRACKET_PAIRS:
            if len(display) >= 2 and display[0] == open_c and display[-1] == close_c:
                display = display[1:-1].strip()
                break
        display = display.strip()
        if display == before:
            break
    if not display:
        return None
    return display, display.casefold()


async def resolve_or_create_topic(
    db: Any,
    *,
    session_id: str,
    identity_mode: str,
    display_label: str,
    normalized_label: str,
) -> str:
    """Resolve a normalized label inside the SAME open transaction.

    Dual inspection: canonical memory_topics.normalized_label AND
    memory_topic_aliases.normalized_alias, strictly same scope.

    - neither → create a server-owned topic_id
    - exactly one → reuse it
    - both with the SAME topic_id → reuse it
    - both with DIFFERENT topic_ids → fail closed (topic_conflict, internal
      data-integrity failure — never a silent pick, never a merge)
    """
    canonical = await (
        await db.execute(
            """SELECT topic_id FROM memory_topics
                WHERE session_id=? AND identity_mode=? AND normalized_label=?""",
            (session_id, identity_mode, normalized_label),
        )
    ).fetchall()
    alias = await (
        await db.execute(
            """SELECT t.topic_id
                 FROM memory_topic_aliases a
                 JOIN memory_topics t
                   ON t.topic_id = a.topic_id
                  AND t.session_id = a.session_id
                  AND t.identity_mode = a.identity_mode
                WHERE a.session_id=? AND a.identity_mode=?
                  AND a.normalized_alias=?""",
            (session_id, identity_mode, normalized_label),
        )
    ).fetchall()
    ids = {str(r["topic_id"]) for r in canonical} | {str(r["topic_id"]) for r in alias}
    if len(ids) > 1:
        raise MemoryValidationError(
            "topic_conflict",
            f"normalized_label={normalized_label!r} resolves to multiple topics",
        )
    if len(ids) == 1:
        return next(iter(ids))

    topic_id = str(uuid.uuid4())
    now = _utc_now()
    await db.execute(
        """INSERT INTO memory_topics(
               topic_id, session_id, identity_mode, normalized_label,
               display_label, created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (topic_id, session_id, identity_mode, normalized_label, display_label, now, now),
    )
    return topic_id


async def list_topics(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
) -> dict[str, Any]:
    """Scoped topic list: active-fact counts only, zero-active hidden."""
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = (session_id or "").strip()
    if not session:
        raise MemoryValidationError("empty_session_id", "session_id is required")

    db = await get_db(wl, "memory")
    try:
        rows = await (
            await db.execute(
                """SELECT t.topic_id, t.display_label,
                          COUNT(f.fact_id) AS fact_count
                     FROM memory_topics t
                     LEFT JOIN stable_facts f
                       ON f.topic_id = t.topic_id
                      AND f.session_id = t.session_id
                      AND f.identity_mode = t.identity_mode
                      AND f.state = 'active'
                    WHERE t.session_id=? AND t.identity_mode=?
                    GROUP BY t.topic_id, t.display_label
                   HAVING COUNT(f.fact_id) > 0
                    ORDER BY t.normalized_label ASC, t.topic_id ASC""",
                (session, mode),
            )
        ).fetchall()
        topics = [
            {
                "topic_id": str(row["topic_id"]),
                "display_label": str(row["display_label"] or ""),
                "fact_count": int(row["fact_count"]),
            }
            for row in rows
        ]
        return {
            "scope": {
                "session_id": session,
                "worldline": wl,
                "identity_mode": mode,
            },
            "topics": topics,
        }
    finally:
        await db.close()


async def rename_topic(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    topic_id: str,
    display_label: Any,
) -> dict[str, Any]:
    """Rename one topic; presentation-only, no merges (plan §8.3 / §7.3).

    The OLD normalized label becomes an alias of the SAME topic so future
    model proposals using the old wording still resolve to it. Renaming back
    to one of this topic's own aliases is allowed (that alias row is deleted
    before the label becomes canonical). Any collision with another topic's
    canonical label or alias fails closed with topic_label_conflict.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = (session_id or "").strip()
    if not session:
        raise MemoryValidationError("empty_session_id", "session_id is required")
    tid = (topic_id or "").strip()
    if not tid:
        raise MemoryValidationError("empty_topic_id", "topic_id is required")
    if not isinstance(display_label, str):
        raise MemoryValidationError(
            "invalid_display_label", "display_label must be a string"
        )
    prepared = prepare_topic_label(display_label)
    if prepared is None:
        raise MemoryValidationError(
            "empty_display_label", "display_label is required"
        )
    new_display, new_norm = prepared

    db = await get_db(wl, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (
            await db.execute(
                """SELECT topic_id, normalized_label, display_label
                     FROM memory_topics
                    WHERE topic_id=? AND session_id=? AND identity_mode=?""",
                (tid, session, mode),
            )
        ).fetchone()
        if row is None:
            raise MemoryValidationError("topic_not_found", f"topic_id={tid}")
        old_norm = str(row["normalized_label"])
        now = _utc_now()

        if new_norm == old_norm:
            # Presentation spelling/case change only: no redundant alias.
            await db.execute(
                """UPDATE memory_topics
                   SET display_label=?, updated_at=?
                 WHERE topic_id=? AND session_id=? AND identity_mode=?""",
                (new_display, now, tid, session, mode),
            )
            await db.commit()
            return {
                "topic_id": tid,
                "display_label": new_display,
                "normalized_label": old_norm,
                "scope": {
                    "session_id": session,
                    "worldline": wl,
                    "identity_mode": mode,
                },
            }

        # CASE B: new normalized label must not belong to another topic.
        other_canonical = await (
            await db.execute(
                """SELECT topic_id FROM memory_topics
                    WHERE session_id=? AND identity_mode=?
                      AND normalized_label=? AND topic_id != ?""",
                (session, mode, new_norm, tid),
            )
        ).fetchone()
        if other_canonical is not None:
            raise MemoryValidationError(
                "topic_label_conflict",
                f"normalized_label={new_norm!r} is another topic's canonical label",
            )
        other_alias = await (
            await db.execute(
                """SELECT topic_id FROM memory_topic_aliases
                    WHERE session_id=? AND identity_mode=?
                      AND normalized_alias=? AND topic_id != ?""",
                (session, mode, new_norm, tid),
            )
        ).fetchone()
        if other_alias is not None:
            raise MemoryValidationError(
                "topic_label_conflict",
                f"normalized_label={new_norm!r} is another topic's alias",
            )
        # Rename-back: new_norm is an alias OF THIS topic → drop it first.
        await db.execute(
            """DELETE FROM memory_topic_aliases
                WHERE session_id=? AND identity_mode=?
                  AND normalized_alias=? AND topic_id=?""",
            (session, mode, new_norm, tid),
        )
        # The old canonical label becomes an alias of THIS topic; it must not
        # collide with another topic's canonical label or alias.
        other_old_label = await (
            await db.execute(
                """SELECT topic_id FROM memory_topics
                    WHERE session_id=? AND identity_mode=?
                      AND normalized_label=? AND topic_id != ?""",
                (session, mode, old_norm, tid),
            )
        ).fetchone()
        if other_old_label is not None:
            raise MemoryValidationError(
                "topic_label_conflict",
                f"old label {old_norm!r} is another topic's canonical label",
            )
        other_old_alias = await (
            await db.execute(
                """SELECT topic_id FROM memory_topic_aliases
                    WHERE session_id=? AND identity_mode=?
                      AND normalized_alias=? AND topic_id != ?""",
                (session, mode, old_norm, tid),
            )
        ).fetchone()
        if other_old_alias is not None:
            raise MemoryValidationError(
                "topic_label_conflict",
                f"old label {old_norm!r} is another topic's alias",
            )
        await db.execute(
            """UPDATE memory_topics
               SET normalized_label=?, display_label=?, updated_at=?
             WHERE topic_id=? AND session_id=? AND identity_mode=?""",
            (new_norm, new_display, now, tid, session, mode),
        )
        await db.execute(
            """INSERT INTO memory_topic_aliases(
                   session_id, identity_mode, normalized_alias, topic_id
               ) VALUES(?,?,?,?)""",
            (session, mode, old_norm, tid),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    return {
        "topic_id": tid,
        "display_label": new_display,
        "normalized_label": new_norm,
        "scope": {"session_id": session, "worldline": wl, "identity_mode": mode},
    }
