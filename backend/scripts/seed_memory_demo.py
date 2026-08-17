"""Idempotent Memory v11 demo density: 10 topics × 5 facts × 4 scopes.

Additive only. Reuses existing topic labels. Skips facts whose display_text
already exists in the same scope. Never deletes user rows.

Usage:
  backend\\.venv\\Scripts\\python.exe scripts\\seed_memory_demo.py
  backend\\.venv\\Scripts\\python.exe scripts\\seed_memory_demo.py --session-id <id>

With no --session-id, discovers UUID sessions from control.session_worldlines
only (never leftover test ids in history). Desktop session key:
localStorage.amadeus_pc_session_id.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_db
from app.services.memory_v11.repository import create_stable_fact
from app.services.memory_v11.topics import prepare_topic_label, resolve_or_create_topic

WORLDLINES = ("steins_gate", "beta")
IDENTITIES = ("okabe", "self")
HISTORICAL_DEMO = "78c36ad1-3612-4d0f-aff8-12208836d5a8"
SESSION_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

TOPICS = (
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

# Three stable facts + two lived-episode facts per topic (still topic-attached
# stable_facts — v11 experiences have no topic_id in Overview projection).
FACTS: dict[str, tuple[str, ...]] = {
    "实验": (
        "认为对照实验不可省略",
        "倾向先复现再下结论",
        "会把失败次数记进笔记",
        "上次通宵推演时把整面白板写满了假设",
        "实验结束后核对时间戳，发现比预想晚了二十分钟",
    ),
    "未来道具": (
        "未来道具的命名很认真",
        "会为道具编号并归档",
        "认为可用性比噱头重要",
        "第一次展示新道具时因为编号对不上当场停住",
        "试作偏差记进本子后，当天又改了一版外壳",
    ),
    "饮品": (
        "更常喝热饮而不是冰饮",
        "工作时会把杯子放在键盘左侧",
        "会记得谁点过什么饮料",
        "深夜实验时连续续了两杯热饮",
        "有一次差点把杯子碰倒在实验笔记上",
    ),
    "实验室": (
        "实验室座位几乎固定",
        "进门后会先看设备指示灯",
        "认为实验室是工作场所不是舞台",
        "那天进门发现指示灯全暗，先把仪器逐台复位",
        "公共桌面被别人摊开后，花了十分钟恢复可复现状态",
    ),
    "世界线": (
        "会区分观测到的变化和推测",
        "不把世界线差异说成情绪隐喻",
        "拒绝没有出处的世界线断言",
        "记录分歧点时把证据和时间并排写了三行",
        "切换视角前先把范围写完整，才肯继续往下说",
    ),
    "日常": (
        "通勤路上会想未完成的问题",
        "会把日常观察写进短句",
        "把日常节奏当成实验背景",
        "那次绕路购物时还在核对未完成条目",
        "睡眠不足的早上仍先把昨夜观察补进笔记",
    ),
    "研究": (
        "阅读论文会先看方法部分",
        "引用时要求可回溯",
        "研究笔记按主题而不是心情归档",
        "把开放问题单独列在页边后，当天又划掉了两条",
        "拒绝一篇无法复核的结论，把它从引用清单里拿掉",
    ),
    "观测": (
        "观测记录必须带时间",
        "重复观测优先于一次印象",
        "观测和解释必须分开写",
        "异常单独标出后，第二次观测才确认它还在",
        "有人把噪声写成规律时，当场要求重写那一行",
    ),
    "通话": (
        "通话后会留下可检索摘要",
        "不把未证实的传闻写进事实",
        "重要约定会再确认一次",
        "长通话拆成三条短记录后才肯合上本子",
        "对方提到的专有名词，挂断后立刻补进摘要",
    ),
    "时间": (
        "对日期和时刻很敏感",
        "会把期限写进可执行条目",
        "认为时间戳是证据的一部分",
        "延迟时先写下原因，而不是先写情绪",
        "计划变更当天补了一条痕迹，避免口头约定蒸发",
    ),
}


def _scope_text(worldline: str, identity: str, text: str) -> str:
    tag = "SG" if worldline == "steins_gate" else "β"
    who = "OKABE" if identity == "okabe" else "SELF"
    return f"{text}（{tag}·{who}）"


async def _topic_exists_text(db, session_id: str, identity: str, text: str) -> bool:
    row = await (
        await db.execute(
            """SELECT 1
                 FROM stable_facts f
                 JOIN stable_fact_versions v
                   ON v.fact_id = f.fact_id AND v.version_no = f.active_version
                WHERE f.session_id=? AND f.identity_mode=? AND f.state='active'
                  AND f.deleted_at IS NULL AND v.display_text=?""",
            (session_id, identity, text),
        )
    ).fetchone()
    return row is not None


async def _collect_ids(db, query: str) -> set[str]:
    found: set[str] = set()
    try:
        rows = await (await db.execute(query)).fetchall()
    except Exception:
        return found
    for row in rows:
        value = str(row[0] or "").strip()
        if value:
            found.add(value)
    return found


async def discover_sessions() -> list[str]:
    """Live desktop sessions only.

    History/memory tables in the same APPDATA tree contain leftover test
    session_ids (ack-ws-*, conversation-crud-*, owner-*). Never seed those.
    """
    found: set[str] = set()
    control = await get_db("steins_gate", "control")
    try:
        found |= await _collect_ids(control, "SELECT session_id FROM session_worldlines")
        found |= await _collect_ids(control, "SELECT session_id FROM session_conversation_selections")
    finally:
        await control.close()

    live = [sid for sid in found if SESSION_UUID.match(sid)]
    if HISTORICAL_DEMO not in live:
        # Include the known SG demo row only if it already exists as a UUID session.
        live.append(HISTORICAL_DEMO)
    live = list(dict.fromkeys(live))
    if HISTORICAL_DEMO in live:
        return [HISTORICAL_DEMO] + sorted(sid for sid in live if sid != HISTORICAL_DEMO)
    return sorted(live)


async def seed_scope(session_id: str, worldline: str, identity: str) -> tuple[int, int]:
    created_topics = 0
    created_facts = 0
    db = await get_db(worldline, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        topic_ids: dict[str, str] = {}
        for label in TOPICS:
            prepared = prepare_topic_label(label)
            assert prepared is not None
            display, norm = prepared
            before = await (
                await db.execute(
                    """SELECT topic_id FROM memory_topics
                        WHERE session_id=? AND identity_mode=? AND normalized_label=?""",
                    (session_id, identity, norm),
                )
            ).fetchone()
            topic_id = await resolve_or_create_topic(
                db,
                session_id=session_id,
                identity_mode=identity,
                display_label=display,
                normalized_label=norm,
            )
            if before is None:
                created_topics += 1
            topic_ids[label] = topic_id
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    for label, lines in FACTS.items():
        topic_id = topic_ids[label]
        for line in lines:
            text = _scope_text(worldline, identity, line)
            db = await get_db(worldline, "memory")
            try:
                exists = await _topic_exists_text(db, session_id, identity, text)
            finally:
                await db.close()
            if exists:
                continue
            await create_stable_fact(
                session_id=session_id,
                worldline=worldline,
                identity_mode=identity,
                display_text=text,
                semantic_json={
                    "subject": "user",
                    "predicate": "states",
                    "object": {"text": text, "demo": "v11-density"},
                },
                topic_id=topic_id,
            )
            created_facts += 1
    return created_topics, created_facts


async def seed_all(session_id: str) -> dict[str, tuple[int, int]]:
    report: dict[str, tuple[int, int]] = {}
    for worldline in WORLDLINES:
        for identity in IDENTITIES:
            report[f"{worldline}/{identity}"] = await seed_scope(session_id, worldline, identity)
    return report


async def seed_sessions(session_ids: list[str]) -> int:
    grand_topics = 0
    grand_facts = 0
    for session_id in session_ids:
        print(f"session {session_id}")
        report = await seed_all(session_id)
        total_topics = sum(item[0] for item in report.values())
        total_facts = sum(item[1] for item in report.values())
        grand_topics += total_topics
        grand_facts += total_facts
        for key, (topics, facts) in report.items():
            print(f"  {key}: +{topics} topics, +{facts} facts")
        print(f"  total: +{total_topics} topics, +{total_facts} facts")
    print(f"grand: {len(session_ids)} sessions, +{grand_topics} topics, +{grand_facts} facts")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed 10×5 Memory demo facts per worldline and identity")
    parser.add_argument(
        "--session-id",
        action="append",
        dest="session_ids",
        default=[],
        help="desktop amadeus_pc_session_id (repeatable). Omit to discover live sessions.",
    )
    args = parser.parse_args()
    explicit = [item.strip() for item in args.session_ids if item and item.strip()]
    if explicit:
        session_ids = list(dict.fromkeys(explicit))
    else:
        session_ids = asyncio.run(discover_sessions())
        if not session_ids:
            print("no live sessions found; pass --session-id <amadeus_pc_session_id>", file=sys.stderr)
            return 2
        print("discovered sessions:")
        for sid in session_ids:
            print(f"  {sid}")
    return asyncio.run(seed_sessions(session_ids))


if __name__ == "__main__":
    raise SystemExit(main())
