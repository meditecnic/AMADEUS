"""Remove mistaken demo-seed rows from leftover non-UUID test sessions.

Only deletes stable facts whose semantic_json contains demo=v11-density
and whose session_id is not a desktop UUID. Never touches UUID sessions.

Usage:
  backend\\.venv\\Scripts\\python.exe scripts\\purge_mistaken_demo_seed.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_db

WORLDLINES = ("steins_gate", "beta")
SESSION_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
SEED_LABELS = (
    "实验",
    "未来道具",
    "饮品",
    "实验室",
    "世界线",
    "日常",
    "研究",
    "观测",
    "通话",
    "时间",
)


async def _ids(db, sql: str, params: tuple = ()) -> list[str]:
    rows = await (await db.execute(sql, params)).fetchall()
    return [str(row[0]) for row in rows]


async def purge_worldline(worldline: str) -> dict[str, int]:
    db = await get_db(worldline, "memory")
    deleted_facts = 0
    deleted_topics = 0
    uuid_demo_before = 0
    try:
        await db.execute("BEGIN IMMEDIATE")
        uuid_demo_before = int(
            (
                await (
                    await db.execute(
                        """SELECT COUNT(*) AS n
                             FROM stable_facts f
                             JOIN stable_fact_versions v
                               ON v.fact_id=f.fact_id AND v.version_no=f.active_version
                            WHERE v.semantic_json LIKE '%v11-density%'""",
                    )
                ).fetchone()
            )["n"]
        )
        fact_ids = await _ids(
            db,
            """SELECT f.fact_id
                 FROM stable_facts f
                 JOIN stable_fact_versions v
                   ON v.fact_id=f.fact_id AND v.version_no=f.active_version
                WHERE v.semantic_json LIKE '%v11-density%'""",
        )
        keep: list[str] = []
        drop: list[str] = []
        for fact_id in fact_ids:
            row = await (
                await db.execute(
                    "SELECT session_id FROM stable_facts WHERE fact_id=?",
                    (fact_id,),
                )
            ).fetchone()
            session_id = str(row["session_id"] if row else "")
            if SESSION_UUID.match(session_id):
                keep.append(fact_id)
            else:
                drop.append(fact_id)

        for fact_id in drop:
            await db.execute("DELETE FROM stable_fact_evidence WHERE fact_id=?", (fact_id,))
            await db.execute(
                "DELETE FROM stable_fact_version_embeddings WHERE fact_id=?",
                (fact_id,),
            )
            await db.execute("DELETE FROM stable_fact_versions WHERE fact_id=?", (fact_id,))
            await db.execute("DELETE FROM stable_facts WHERE fact_id=?", (fact_id,))
        deleted_facts = len(drop)

        topic_rows = await (
            await db.execute(
                """SELECT topic_id, session_id, identity_mode, display_label
                     FROM memory_topics""",
            )
        ).fetchall()
        for row in topic_rows:
            session_id = str(row["session_id"])
            if SESSION_UUID.match(session_id):
                continue
            if str(row["display_label"]) not in SEED_LABELS:
                continue
            remaining = await (
                await db.execute(
                    """SELECT 1 FROM stable_facts
                        WHERE topic_id=? AND session_id=? AND identity_mode=?
                          AND state='active' LIMIT 1""",
                    (row["topic_id"], session_id, row["identity_mode"]),
                )
            ).fetchone()
            if remaining is not None:
                continue
            await db.execute(
                """DELETE FROM memory_topic_aliases
                    WHERE topic_id=? AND session_id=? AND identity_mode=?""",
                (row["topic_id"], session_id, row["identity_mode"]),
            )
            await db.execute(
                """DELETE FROM memory_topics
                    WHERE topic_id=? AND session_id=? AND identity_mode=?""",
                (row["topic_id"], session_id, row["identity_mode"]),
            )
            deleted_topics += 1

        remaining = await (
            await db.execute(
                """SELECT f.session_id
                     FROM stable_facts f
                     JOIN stable_fact_versions v
                       ON v.fact_id=f.fact_id AND v.version_no=f.active_version
                    WHERE v.semantic_json LIKE '%v11-density%'""",
            )
        ).fetchall()
        leftover = [str(row["session_id"]) for row in remaining if not SESSION_UUID.match(str(row["session_id"]))]
        uuid_demo_after = len(remaining)
        if leftover:
            raise RuntimeError(f"{worldline}: leftover mistaken demo facts: {leftover[:8]}")
        if uuid_demo_after != len(keep):
            raise RuntimeError(
                f"{worldline}: UUID demo count changed {len(keep)} -> {uuid_demo_after}"
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()
    return {
        "uuid_demo": uuid_demo_after,
        "uuid_demo_before": uuid_demo_before,
        "deleted_facts": deleted_facts,
        "deleted_topics": deleted_topics,
    }


async def main() -> int:
    for worldline in WORLDLINES:
        report = await purge_worldline(worldline)
        print(
            f"{worldline}: deleted {report['deleted_facts']} facts, "
            f"{report['deleted_topics']} topics; "
            f"UUID demo kept {report['uuid_demo']} "
            f"(before {report['uuid_demo_before']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
