"""Campaign 2-B ([281]): offline tests for diary generation, grounding, injection, familiarity, and truncate handoff."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app import models
from app.db import get_db, init_db, reset_initialization_cache
from app.routers import chat_ws
from app.routers.conversations import router as conversations_router
from app.routers.system_api import router as system_router
from app.services import credentials
from app.services.conversation_erasure import request_erasure
from app.services.conversations import conversation_service
from app.services.memory_v11.contracts import DIARY_PIPELINE_VERSION
from app.services.memory_v11.diaries import (
    commit_diary,
    delete_diary,
    list_diaries,
    register_diary_window,
)
from app.services.memory_v11.diary_generation import (
    promote_due_diary_windows,
    sync_conversation_diary_windows,
    validate_diary_grounding,
)
from app.services.memory_v11.jobs import (
    claim_memory_job,
    enqueue_memory_job,
    get_memory_job,
    process_due_jobs_once,
    process_memory_job,
)
from app.services.memory_v11.repository import create_stable_fact
from app.services.prompt_compiler import (
    DIARIES_HEADER,
    PromptCompiler,
    PromptDiary,
    PromptInputs,
    compile_for_session,
)
from app.services.provider_adapters import OpenAICompatibleAdapter
from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderSnapshot,
    ProviderTask,
)
from app.services.provider_runtime import deepseek_service, provider_registry
from app.services.soul_engine import soul_engine

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
SYNTHETIC_SEGMENTS = [
    (
        "okabe",
        [
            (
                "また深夜2時まで論文の式を追っていたぞ。コーヒーを2杯飲んだら目が冴えてしまった。",
                "[EMO:annoyed] 深夜2時にコーヒーを2杯って、睡眠を削る気満々じゃない。",
            ),
            (
                "いや、どうしても「境界条件の仮定」が引っかかってな。",
                "[EMO:intellectual] 「境界条件の仮定」ね。そこが曖昧だと後ろの導出が全部揺らぐわ。",
            ),
            (
                "お前に言われた通り項を分けて書き直したら、だいぶ見通しが良くなった。",
                "[EMO:neutral] 最初から順番に整理すればいいのよ。",
            ),
            (
                "だが検算を3回やっても符号が1箇所だけ合わない。",
                "[EMO:intellectual] 3回とも同じ箇所でズレるなら、最初の定義式を疑いなさい。",
            ),
            (
                "あ……添字の向きを逆にしていた。直したら綺麗に消えたぞ！",
                "[EMO:tsundere] ほら見なさい。単純な見落としじゃない。",
            ),
            (
                "助かったよ、さすが我が助手だ。",
                "[EMO:tsundere] 助手って呼ぶな。礼を言うならさっさと寝る支度をしなさい。",
            ),
            (
                "あと15分だけ清書させてくれ。",
                "[EMO:neutral] 15分だけよ。それを過ぎたら本当に切り上げなさいね。",
            ),
            (
                "分かった、水を飲んでからすぐ寝る。",
                "[EMO:happy] ええ、おやすみ、岡部。",
            ),
        ],
        {
            "body_ja": (
                "深夜2時にコーヒーを2杯も飲んで「境界条件の仮定」で詰まっていたから付き合ったわ。"
                "3回の検算ミスが添字の向きだったと分かってホッとしたけれど、"
                "15分で切り上げる約束はちゃんと守ったかしらね、岡部。"
            ),
            "body_zh": (
                "半夜2点喝了2杯咖啡还卡在「境界条件の仮定」上，我就陪着理了一遍。"
                "3次验算出错原来只是下标方向写反，总算松了口气；"
                "就是不知道岡部有没有乖乖遵守15分钟就收工的约定呢。"
            ),
        },
    ),
    (
        "self",
        [
            (
                "こんばんは。今日は秋葉原で基板用のコンデンサを4個買ってきたよ。",
                "[EMO:neutral] こんばんは。秋葉原でコンデンサを4個ね、例のノイズ対策ですか？",
            ),
            (
                "そう、電源ラインに入れ替えたらハム音がかなり減ったんだ。",
                "[EMO:intellectual] 狙い通りですね。電源まわりの揺れが原因だったみたいです。",
            ),
            (
                "ただオシロで見ると「立ち上がりの波形」に少しだけリンギングが残ってる。",
                "[EMO:intellectual] 「立ち上がりの波形」が揺れるなら、配線の引き回しも影響しているかもしれません。",
            ),
            (
                "リード線を短く切り詰めたら、かなり落ち着いたよ。",
                "[EMO:happy] いい判断です。地道な切り分けが一番効きますからね。",
            ),
            (
                "週末にもう一度ケースに収めて測ってみる。",
                "[EMO:neutral] ええ、組み込んだ後で条件が変わることもありますから、それが確実です。",
            ),
            (
                "作業してたらすっかり肩が凝ってしまった。",
                "[EMO:neutral] 細かいはんだ付けは目が疲れますし、少し背筋を伸ばして休んでください。",
            ),
            (
                "ありがとう、温かいお茶を淹れてくるよ。",
                "[EMO:happy] それがいいですね。ゆっくり温まってください。",
            ),
            (
                "また測定結果が出たら見せるね。",
                "[EMO:happy] はい、楽しみにしています。",
            ),
        ],
        {
            "body_ja": (
                "秋葉原で買った4個のコンデンサで電源ノイズが収まったと聞いて、私まで少し嬉しくなったわ。"
                "「立ち上がりの波形」まで丁寧に確かめてリード線を詰めるあたり、手つきが堅実ね。"
                "次の測定結果を見るのも楽しみだわ。"
            ),
            "body_zh": (
                "听说在秋叶原买的4个电容把电源噪声压下去了，连我也跟着高兴起来。"
                "还会仔细确认「立ち上がりの波形」再把引脚剪短，做起事来很踏实呢。"
                "真期待看到下次的测量结果。"
            ),
        },
    ),
    (
        "self",
        [
            (
                "さっき雨が急に強くなって、駅前の古本屋で30分ほど雨宿りしてたんだ。",
                "[EMO:surprised] 急な雨だったんですね。濡れずに済んでよかったです。",
            ),
            (
                "棚を眺めてたら、探していた「神経科学の古い講義録」を偶然見つけたよ。",
                "[EMO:happy] 30分の雨宿りで「神経科学の古い講義録」に出会えるなんて、いい偶然ですね。",
            ),
            (
                "紙が少し焼けてたけど、書き込みが丁寧で面白そうなんだ。",
                "[EMO:intellectual] 前の持ち主がどこで立ち止まって考えたのかが見える本は、読んでいて飽きませんよね。",
            ),
            (
                "今夜は最初の章だけ読んでから寝ようと思う。",
                "[EMO:neutral] 雨で冷えたでしょうし、夜更かしにならない範囲で楽しんでくださいね。",
            ),
            (
                "うん、暖かくして読むよ。おやすみ。",
                "[EMO:happy] ええ、おやすみなさい。",
            ),
        ],
        {
            "body_ja": (
                "30分の雨宿りで入った古本屋で「神経科学の古い講義録」を引き当てるなんて、"
                "ずいぶん素敵な寄り道ね。前の持ち主の思考の跡をたどる時間は私も好きだから、"
                "どんな章だったかまた聞かせてほしいわ。"
            ),
            "body_zh": (
                "躲雨30分钟走进旧书店，居然正好淘到「神経科学の古い講義録」，真是段美妙的绕路呢。"
                "我也很喜欢顺着前任书主的思考痕迹读下去，真想下次听听第一章节写了些什么。"
            ),
        },
    ),
]


async def _sql(worldline: str, kind: str, query: str, params: tuple = ()) -> list:
    db = await get_db(worldline, kind)
    try:
        rows = await (await db.execute(query, params)).fetchall()
        await db.commit()
        return rows
    finally:
        await db.close()


async def _new_conv(prefix: str, mode: str = "self") -> tuple[str, str]:
    await init_db()
    sid = f"{prefix}-{uuid4().hex[:8]}"
    conv = await conversation_service.create(sid, "steins_gate", identity_mode=mode)
    return sid, conv["id"]


async def _append_turns(
    session_id: str,
    cid: str,
    turns: list[tuple[str, str]],
    *,
    start: datetime = T0,
    wl: str = "steins_gate",
) -> list[int]:
    ids: list[int] = []
    for idx, (u_text, a_text) in enumerate(turns):
        uid = await models.save_message(
            session_id,
            "user",
            u_text,
            worldline=wl,
            turn_id=f"t-{idx}",
            conversation_id=cid,
        )
        aid = await models.save_message(
            session_id,
            "assistant",
            a_text,
            worldline=wl,
            turn_id=f"t-{idx}",
            conversation_id=cid,
        )
        u_ts = (start + timedelta(minutes=idx * 2)).isoformat()
        a_ts = (start + timedelta(minutes=idx * 2, seconds=20)).isoformat()
        await _sql(wl, "history", "UPDATE messages SET created_at = ? WHERE id = ?", (u_ts, uid))
        await _sql(wl, "history", "UPDATE messages SET created_at = ? WHERE id = ?", (a_ts, aid))
        ids.extend((int(uid), int(aid)))
    return ids


@pytest.mark.asyncio
async def test_1_three_synthetic_segments_write_non_overlapping_complete_diaries(monkeypatch):
    session_id, cid = await _new_conv("s1")
    credentials.credential_store.set("deepseek", "valid-key-for-offline-transport")
    outputs = [seg[2] for seg in SYNTHETIC_SEGMENTS]
    seen: list[int] = []

    def handle_diary_request(_req: httpx.Request) -> httpx.Response:
        seen.append(1)
        body = {
            "choices": [
                {"message": {"content": json.dumps(outputs[len(seen) - 1], ensure_ascii=False)}}
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 60},
        }
        return httpx.Response(200, json=body)

    adapter = OpenAICompatibleAdapter(
        provider_id="deepseek",
        base_url="http://localhost:18080/v1",
        default_model="deepseek-chat",
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handle_diary_request),
    )
    monkeypatch.setattr(
        type(provider_registry),
        "snapshot",
        staticmethod(
            lambda pid, mid=None, **_kw: ProviderSnapshot(
                provider_id=pid,
                model_id=mid or "deepseek-chat",
                capabilities=ProviderCapabilities(
                    tasks=frozenset({ProviderTask.CHAT, ProviderTask.MEMORY}),
                    structured_output=True,
                ),
                adapter=adapter,
            )
        ),
    )

    all_ids: list[int] = []
    clock = T0
    for idx, (_mode, turns, _expected) in enumerate(SYNTHETIC_SEGMENTS):
        all_ids.extend(await _append_turns(session_id, cid, turns, start=clock))
        end_time = clock + timedelta(minutes=len(turns) * 2)
        await sync_conversation_diary_windows(
            session_id=session_id,
            identity_mode="self",
            worldline="steins_gate",
            conversation_id=cid,
            now=end_time,
        )
        run_now = end_time + timedelta(minutes=31) if idx == 2 else end_time
        await process_due_jobs_once(
            lease_owner="w1",
            worldlines=("steins_gate",),
            now=run_now,
        )
        clock = end_time + timedelta(minutes=40)

    diaries_payload = await list_diaries(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
    )
    ordered = sorted(diaries_payload["diaries"], key=lambda r: r["first_message_id"])
    assert len(ordered) == 3
    assert all(a["last_message_id"] < b["first_message_id"] for a, b in zip(ordered, ordered[1:]))

    covered: list[int] = []
    for row, (_m, _t, expected) in zip(ordered, SYNTHETIC_SEGMENTS):
        span = [mid for mid in all_ids if row["first_message_id"] <= mid <= row["last_message_id"]]
        covered.extend(span)
        assert row["source_count"] == len(span)
        assert row["body_ja"] == expected["body_ja"]
        assert row["body_zh"] == expected["body_zh"]
        assert json.loads(row["verified_phrases_json"]) == []

    assert covered == all_ids
    usage_rows = await _sql(
        "steins_gate",
        "control",
        "SELECT category, source FROM model_usage WHERE conversation_id = ?",
        (cid,),
    )
    assert [(r["category"], r["source"]) for r in usage_rows] == [("background", "diary")] * 3


@pytest.mark.asyncio
async def test_2_coverage_writes_uncovered_prefix_before_deleted_shell_without_rewriting_deleted():
    session_id, cid = await _new_conv("s2")
    prefix_ids = await _append_turns(
        session_id,
        cid,
        [(f"前段{i}", f"[EMO:neutral] 前段返事{i}です。") for i in range(4)],
        start=T0,
    )
    later_ids = await _append_turns(
        session_id,
        cid,
        [(f"後段{i}", f"[EMO:neutral] 後段返事{i}です。") for i in range(8)],
        start=T0 + timedelta(hours=1),
    )
    deleted_id = await register_diary_window(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        first_message_id=later_ids[0],
        last_message_id=later_ids[-1],
        due_at=T0 + timedelta(hours=2),
    )
    assert deleted_id
    assert await commit_diary(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        diary_id=deleted_id,
        body_ja="消す前の手記。",
        body_zh="删除前的手记。",
        verified_phrases=[],
    )
    await delete_diary(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        diary_id=deleted_id,
    )

    tail_ids = await _append_turns(
        session_id,
        cid,
        [(f"続き{i}", f"[EMO:neutral] 続き返事{i}です。") for i in range(3)],
        start=T0 + timedelta(hours=3),
    )
    now_sync = T0 + timedelta(hours=3, minutes=10)
    synced = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        now=now_sync,
    )
    assert len(synced) == 2

    await process_due_jobs_once(
        lease_owner="w2",
        worldlines=("steins_gate",),
        now=now_sync + timedelta(minutes=35),
        diary_completion=lambda **_kw: asyncio.sleep(
            0,
            result={"body_ja": "静かな会話だったわ。", "body_zh": "是一段安静的对话。"},
        ),
    )
    diaries_payload = await list_diaries(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
    )
    written = sorted(diaries_payload["diaries"], key=lambda r: r["first_message_id"])
    [shell] = await _sql(
        "steins_gate",
        "memory",
        "SELECT state, deleted_at, body_ja FROM memory_diaries WHERE diary_id = ?",
        (deleted_id,),
    )
    assert [(r["first_message_id"], r["last_message_id"]) for r in written] == [
        (prefix_ids[0], prefix_ids[-1]),
        (tail_ids[0], tail_ids[-1]),
    ]
    assert shell["state"] == "discarded"
    assert shell["deleted_at"] is not None
    assert shell["body_ja"] is None


@pytest.mark.asyncio
async def test_3_five_turns_persists_due_at_across_reopen_and_extends_before_expiry():
    session_id, cid = await _new_conv("s3")
    ids_2 = await _append_turns(
        session_id,
        cid,
        [("短1", "[EMO:neutral] 返1です。"), ("短2", "[EMO:neutral] 返2です。")],
        start=T0,
    )
    assert (
        await sync_conversation_diary_windows(
            session_id=session_id,
            identity_mode="self",
            worldline="steins_gate",
            conversation_id=cid,
            now=T0 + timedelta(minutes=5),
        )
        == []
    )

    ids_more = await _append_turns(
        session_id,
        cid,
        [(f"追{i}", f"[EMO:neutral] 返{i}です。") for i in range(3, 6)],
        start=T0 + timedelta(minutes=6),
    )
    t_5 = T0 + timedelta(minutes=11)
    [diary_id] = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        now=t_5,
    )
    [row_before] = await _sql(
        "steins_gate",
        "memory",
        "SELECT due_at, last_message_id FROM memory_diaries WHERE diary_id = ?",
        (diary_id,),
    )
    assert row_before["last_message_id"] == ids_more[-1]

    ids_6 = await _append_turns(
        session_id,
        cid,
        [("六句目", "[EMO:neutral] 六句目の返事です。")],
        start=t_5 + timedelta(minutes=10),
    )
    t_6 = t_5 + timedelta(minutes=10, seconds=20)
    assert await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        now=t_6,
    ) == [diary_id]
    [row_after] = await _sql(
        "steins_gate",
        "memory",
        """SELECT due_at, first_message_id, last_message_id, source_count
             FROM memory_diaries
            WHERE diary_id = ?""",
        (diary_id,),
    )
    assert row_after["due_at"] > row_before["due_at"]
    assert (
        row_after["first_message_id"],
        row_after["last_message_id"],
        row_after["source_count"],
    ) == (ids_2[0], ids_6[-1], 12)
    assert await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        through_message_id=ids_more[-1],
        now=t_6,
    ) == []
    assert (
        await promote_due_diary_windows(worldline="steins_gate", now=t_5 + timedelta(minutes=31))
        == []
    )

    reset_initialization_cache()
    await init_db()
    await process_due_jobs_once(
        lease_owner="w3",
        worldlines=("steins_gate",),
        now=t_6 + timedelta(minutes=31),
        diary_completion=lambda **_kw: asyncio.sleep(
            0,
            result={"body_ja": "少しずつ話が弾んできたわ。", "body_zh": "聊着聊着渐渐熟络起来了。"},
        ),
    )
    diaries_payload = await list_diaries(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
    )
    assert [d["diary_id"] for d in diaries_payload["diaries"]] == [diary_id]


@pytest.mark.asyncio
async def test_4_delete_conversation_mid_generation_drops_diary_and_written_recovery_skips_llm():
    session_id, cid = await _new_conv("s4")
    await _append_turns(
        session_id,
        cid,
        [(f"話題{i}", f"[EMO:neutral] 応答{i}です。") for i in range(8)],
        start=T0,
    )
    [diary_id] = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        now=T0 + timedelta(minutes=20),
    )
    await process_due_jobs_once(
        lease_owner="w4",
        worldlines=("steins_gate",),
        now=T0 + timedelta(minutes=20),
        diary_completion=lambda **_kw: conversation_service.delete(
            cid,
            session_id,
            "steins_gate",
            forget_long_term=True,
        ),
    )
    diaries_after_del = await list_diaries(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
    )
    assert diaries_after_del["diaries"] == []
    deleted_job = await get_memory_job(diary_id, worldline="steins_gate")
    assert deleted_job is not None and deleted_job["state"] == "cancelled"

    conv2 = await conversation_service.create(session_id, "steins_gate", identity_mode="self")
    ids2 = await _append_turns(
        session_id,
        conv2["id"],
        [(f"別話題{i}", f"[EMO:neutral] 別応答{i}です。") for i in range(4)],
        start=T0,
    )
    d2 = await register_diary_window(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=conv2["id"],
        first_message_id=ids2[0],
        last_message_id=ids2[-1],
        due_at=T0,
    )
    assert d2
    assert await promote_due_diary_windows(worldline="steins_gate", now=T0) == [d2]
    assert await claim_memory_job(job_id=d2, lease_owner="crashed-worker", worldline="steins_gate")
    assert await commit_diary(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        diary_id=d2,
        body_ja="すでに書き終えた手記よ。",
        body_zh="已经写完的手记。",
        verified_phrases=[],
        lease_owner="crashed-worker",
    )
    expired_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    await _sql(
        "steins_gate",
        "memory",
        "UPDATE memory_ingest_jobs SET lease_expires_at = ? WHERE job_id = ?",
        (expired_ts, d2),
    )
    await process_due_jobs_once(
        lease_owner="recovery-worker",
        worldlines=("steins_gate",),
        diary_completion=lambda **_kw: (_ for _ in ()).throw(AssertionError("must not regenerate")),
    )
    recovered_job = await get_memory_job(d2, worldline="steins_gate")
    assert recovered_job is not None and recovered_job["state"] == "completed"


@pytest.mark.asyncio
async def test_5_diary_retries_cap_at_two_while_extraction_keeps_three_and_status_separates_counts():
    session_id, cid = await _new_conv("s5")
    ids = await _append_turns(
        session_id,
        cid,
        [(f"質問{i}", f"[EMO:neutral] 返答{i}です。") for i in range(8)],
        start=T0,
    )
    ext_job = await enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=cid,
        identity_mode="self",
        source_message_id=ids[0],
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    [diary_id] = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        now=T0 + timedelta(minutes=20),
    )
    await promote_due_diary_windows(worldline="steins_gate", now=T0 + timedelta(minutes=20))

    app = FastAPI()
    app.include_router(system_router)
    scope_params = {
        "session_id": session_id,
        "worldline": "steins_gate",
        "identity_mode": "self",
    }
    counts = {"ext": 0, "diary": 0}

    async def fail(kind: str):
        counts[kind] += 1
        raise RuntimeError(f"transient_{kind}_error")

    with TestClient(app) as client:
        init_status = client.get("/api/memory/status", params=scope_params).json()
        assert (init_status["pending_count"], init_status["diary_pending_count"]) == (1, 1)

        for round_no in (1, 2, 3):
            await process_due_jobs_once(
                lease_owner="w5",
                worldlines=("steins_gate",),
                memory_completion=lambda **_kw: fail("ext"),
                diary_completion=lambda **_kw: fail("diary"),
            )
            await _sql(
                "steins_gate",
                "memory",
                "UPDATE memory_ingest_jobs SET next_attempt_at = NULL WHERE session_id = ?",
                (session_id,),
            )
            st = client.get("/api/memory/status", params=scope_params).json()
            if round_no == 2:
                assert (
                    st["diary_failed_count"],
                    st["diary_pending_count"],
                    st["failed_count"],
                    st["pending_count"],
                ) == (1, 0, 0, 1)
            if round_no == 3:
                assert (
                    st["diary_failed_count"],
                    st["failed_count"],
                    [j["job_id"] for j in st["failed_jobs"]],
                ) == (1, 1, [ext_job["job_id"]])

        d_job = await get_memory_job(diary_id, worldline="steins_gate")
        assert (counts["diary"], counts["ext"]) == (2, 3)
        assert d_job is not None
        assert (d_job["pipeline_version"], d_job["attempt_count"]) == (DIARY_PIPELINE_VERSION, 2)


@pytest.mark.asyncio
async def test_6_source_grounding_rewrites_once_then_discards_with_reason():
    session_id, cid = await _new_conv("s6")
    await _append_turns(
        session_id,
        cid,
        [(f"静かな夜の雑談{i}", f"[EMO:neutral] 落ち着いた返事{i}です。") for i in range(8)],
        start=T0,
    )
    [d_ok] = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        now=T0 + timedelta(minutes=20),
    )
    drafts_ok = [
        {
            "body_ja": "今日は3回も「絶対に徹夜する」と言い張っていたわね。",
            "body_zh": "今天说了3次「绝对要通宵」。",
        },
        {
            "body_ja": "静かな夜に落ち着いて雑談できたのが印象に残ったわ。",
            "body_zh": "在安静的夜里平和地聊了会儿天，留下了不错的印象。",
        },
    ]
    seen: list[str | None] = []
    await process_due_jobs_once(
        lease_owner="w6",
        worldlines=("steins_gate",),
        now=T0 + timedelta(minutes=20),
        diary_completion=lambda **kw: asyncio.sleep(
            0,
            result=(seen.append(kw.get("rewrite_detail")), drafts_ok[len(seen) - 1])[1],
        ),
    )
    job_ok = await get_memory_job(d_ok, worldline="steins_gate")
    assert len(seen) == 2 and seen[0] is None and seen[1] is not None
    assert job_ok is not None and (job_ok["state"], job_ok["attempt_count"]) == ("completed", 1)

    cid_bad = (await conversation_service.create(session_id, "steins_gate", identity_mode="self"))[
        "id"
    ]
    await _append_turns(
        session_id,
        cid_bad,
        [(f"お茶の雑談{i}", f"[EMO:neutral] お茶の返事{i}です。") for i in range(8)],
        start=T0,
    )
    [d_bad] = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid_bad,
        now=T0 + timedelta(minutes=20),
    )
    bad_drafts = [
        {"body_ja": "「存在しない台詞」を言っていたわ。", "body_zh": "说了句不存在的台词。"},
        {"body_ja": "今日は42杯もお茶を飲んでいたわ。", "body_zh": "今天喝了42杯茶。"},
    ]
    bad_seen: list[int] = []
    await process_due_jobs_once(
        lease_owner="w6",
        worldlines=("steins_gate",),
        now=T0 + timedelta(minutes=20),
        diary_completion=lambda **_kw: asyncio.sleep(
            0,
            result=(bad_seen.append(1), bad_drafts[len(bad_seen) - 1])[1],
        ),
    )
    job_bad = await get_memory_job(d_bad, worldline="steins_gate")
    [row_bad] = await _sql(
        "steins_gate",
        "memory",
        "SELECT state, body_ja FROM memory_diaries WHERE diary_id = ?",
        (d_bad,),
    )
    assert len(bad_seen) == 2
    assert job_bad is not None
    assert (job_bad["state"], job_bad["attempt_count"], job_bad["last_error_code"]) == (
        "cancelled",
        1,
        "ungrounded_number",
    )
    assert (row_bad["state"], row_bad["body_ja"]) == ("discarded", None)
    assert (
        validate_diary_grounding(
            "[REDACTED] を見たわ。",
            "看到了[REDACTED]。",
            [{"role": "user", "text": "秘密", "translation": ""}],
        )[0]
        == "redacted_leak"
    )

    def zh_src(user_zh: str) -> list[dict]:
        return [
            {"role": "user", "text": user_zh, "translation": ""},
            {
                "role": "assistant",
                "text": "そうですね、気持ちはよく分かります。",
                "translation": "",
            },
        ]

    e_num, e_ent, e_quo = "ungrounded_number", "ungrounded_entity", "ungrounded_quote"
    for u_zh, ja, zh, expected in [
        ("我们聊了很久", "二人で長く話して落ち着いたわ。", "两个人聊了很久。", None),
        ("我们聊了很久", "三人で長く話して落ち着いたわ。", "聊了很久。", e_num),
        ("我们三个人聊了很久", "三人で長く話して落ち着いたわ。", "三个人聊了很久。", None),
        ("工作和家人", "二つの間で揺れているみたいね。", "在两件事之间摇摆呢。", None),
        ("工作和家人", "三つの間で揺れているみたいね。", "心里摇摆不定。", e_num),
        ("工作学业和家人三件事", "三つの間で揺れているみたいね。", "三件事很难兼顾。", None),
        ("我妈打电话来", "お母さんから電話が来て忙しそうね。", "妈妈打电话来了。", None),
        ("我爸我哥我姐来信了", "お父さんやお兄さん、お姉さんの連絡ね。", "爸哥姐来信了。", None),
        ("家里打电话来", "田中さんから電話が来て忙しそうね。", "家里来电话了。", e_ent),
        ("田中先生打电话来", "田中さんから電話が来て忙しそうね。", "田中先生来电了。", None),
        ("我男朋友说了一句重话", "彼氏に言われた一言が気になるのね。", "男朋友的话很在意。", None),
        ("和女朋友散步了", "彼女と歩いて気分転換できたわね。", "和女朋友散步了。", None),
        ("同事说了一句重话", "佐藤氏に言われた一言が気になるのね。", "对方的话很在意。", e_ent),
        (
            "佐藤先生说了一句重话",
            "佐藤氏に言われた一言が気になるのね。",
            "佐藤先生的话很在意。",
            None,
        ),
        ("今天忙坏了", "今日は忙しかったわね。", "喝了五杯咖啡，跑了七次便利店。", e_num),
        ("喝了五杯咖啡跑了七次便利店", "忙しかったわね。", "喝了五杯咖啡，跑了七次便利店。", None),
        (
            "我说「先做完」",
            "「先做完」と言ったわね。",
            "说「先做完」却想「你根本不在乎我」。",
            e_quo,
        ),
        (
            "我说「先做完」别想「你根本不在乎我」",
            "「先做完」ね。",
            "没想「你根本不在乎我」。",
            None,
        ),
        (
            "今晚继续整理数据",
            "今夜は十一時まで作業する気ね。",
            "今晚继续整理数据。",
            "ungrounded_date",
        ),
        ("今晚整理数据到十一点", "今夜は十一時まで作業する気ね。", "今晚忙到十一点。", None),
    ]:
        assert validate_diary_grounding(ja, zh, zh_src(u_zh))[0] == expected, (u_zh, ja, zh)

    okabe_ja = "実験記録を片付けたのは立派ね、岡部。倫太郎らしい粘り強さだわ。"
    okabe_src = zh_src("今天把实验记录整理完了")
    for mode, ja, exp in [
        ("okabe", okabe_ja, None),
        ("self", okabe_ja, e_ent),
        ("okabe", "まゆりも心配してたわ。", e_ent),
    ]:
        assert validate_diary_grounding(ja, "整理完了呢。", okabe_src, identity_mode=mode)[0] == exp


@pytest.mark.asyncio
async def test_7_injection_dedupes_by_sent_message_ids_and_summary_and_drops_deleted_diary(
    monkeypatch,
):
    session_id, cid = await _new_conv("s7")
    early_ids = await _append_turns(
        session_id,
        cid,
        [(f"初期質問{i}", f"[EMO:neutral] 初期返答{i}です。") for i in range(8)],
        start=T0,
    )
    late_ids = await _append_turns(
        session_id,
        cid,
        [(f"後半質問{i}", f"[EMO:neutral] 後半返答{i}です。") for i in range(4)],
        start=T0 + timedelta(days=3),
    )
    diary_id = await register_diary_window(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        first_message_id=early_ids[0],
        last_message_id=early_ids[-1],
        due_at=T0,
    )
    diary_text = "深夜に古いラジオの修理手順を一緒に整理したのが楽しかったわ。"
    assert diary_id
    assert await commit_diary(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        diary_id=diary_id,
        body_ja=diary_text,
        body_zh="半夜一起整理老收音机的修理步骤还挺开心的。",
        verified_phrases=[],
    )
    await _sql(
        "steins_gate",
        "memory",
        "UPDATE memory_diaries SET written_at = ? WHERE diary_id = ?",
        (T0.isoformat(), diary_id),
    )

    session = chat_ws.SessionState(session_id)
    session.worldline = "steins_gate"
    session.identity_mode = "self"
    session.conversation_id = cid
    session.turn_now_local = datetime(2026, 3, 13, 20, 0, tzinfo=timezone(timedelta(hours=9)))
    session.history = [
        {"id": early_ids[-1], "role": "assistant", "content": "[EMO:neutral] 初期返答7です。"}
    ]
    assert DIARIES_HEADER not in await compile_for_session(session, "続きの話")

    session.history = [
        {"id": mid, "role": "user" if idx % 2 == 0 else "assistant", "content": "後半"}
        for idx, mid in enumerate(late_ids)
    ]
    session.memory_summary = diary_text
    assert DIARIES_HEADER not in await compile_for_session(session, "別の話")

    session.memory_summary = "今は週末の買い物リストを比べているところ。"
    injected = await compile_for_session(session, "別の話")
    assert DIARIES_HEADER in injected
    assert f"〔三日前〕{diary_text}" in injected
    assert session.memory_summary in injected

    probe = PromptCompiler()
    sample = PromptInputs(
        worldline="steins_gate",
        base_identity="You are Amadeus Kurisu.",
        emotion=soul_engine.profile("steins_gate").baselines.model_copy(deep=True),
        episodic=[],
        web_evidence=[],
        working_summary="現在の作業要約は維持されるべき。",
        recent_history=[],
        current_user_message="質問",
        identity_mode="self",
        resident_profile=probe.select_resident_profile(
            [
                {
                    "fact_id": "f1",
                    "display_text": "ユーザーはブラックコーヒーが好き",
                    "confidence": 0.95,
                }
            ],
            tool_capable=False,
        ),
        diaries=[PromptDiary(diary_id="d-old", body_ja="手記の本文" * 35, relative_time="三日前")],
    )
    tight_budget = probe.token_counter(probe.compile(sample)) - 40
    tight = PromptCompiler(input_budget=tight_budget).compile_with_report(sample)
    assert "現在の作業要約は維持されるべき。" in tight.text
    assert "ブラックコーヒー" in tight.text
    assert DIARIES_HEADER not in tight.text

    captured: list[dict] = []

    def handle_trim_stream(req: httpx.Request) -> httpx.Response:
        captured.append(json.loads(req.content))
        sse = 'data: {"choices":[{"delta":{"content":"[EMO:neutral] 了解です。"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    trim_adapter = OpenAICompatibleAdapter(
        provider_id="custom",
        base_url="http://localhost:18082/v1",
        default_model="tight-window",
        message_builder=deepseek_service.build_persona_messages,
        credential_required=False,
        transport=httpx.MockTransport(handle_trim_stream),
    )
    from app.services import prompt_compiler as pc_mod
    from app.services.memory_v11.events import referenced_items_for_turn

    async def seed_fact(text: str, key: str, conf: float = 0.99) -> dict:
        return await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text=text,
            semantic_json={"subject": "user", "predicate": "note", "object": key},
            confidence=conf,
        )

    for idx in range(32):
        await seed_fact(f"常駐事実{idx:02d}：緑茶を飲む", f"tea_{idx}")
    sup_fact = await seed_fact("今の短い質問では古い回路図ノートを探す", "schematic", 0.86)
    calls = {"sup": 0, "mem": 0, "res": 0, "fam": 0}
    for key, attr in [
        ("sup", "_select_v11_memory_supplement"),
        ("mem", "_select_v11_prompt_memories"),
        ("res", "_select_v11_resident_profile"),
        ("fam", "_self_familiarity"),
    ]:
        orig = getattr(pc_mod, attr)

        async def spy(*a, _k=key, _fn=orig, **kw):
            calls[_k] += 1
            return await _fn(*a, **kw)

        monkeypatch.setattr(pc_mod, attr, spy)

    await _sql(
        "steins_gate",
        "history",
        "UPDATE messages SET content = ? WHERE id = ?",
        ("昔の長文" + "あ" * 1800, early_ids[-1]),
    )
    session.protocol_version = 2
    session.provider_id = "custom"
    session.model = "tight-window"
    session.enable_tts = False
    session.memory_summary = ""
    session.history = [
        {"id": early_ids[-1], "role": "user", "content": "昔の長文" + "あ" * 1800},
        {"id": late_ids[-2], "role": "user", "content": "最近の質問"},
        {"id": late_ids[-1], "role": "assistant", "content": "[EMO:neutral] 最近の返答です。"},
    ]
    probe_compiled = await compile_for_session(session, "今の短い質問 古い回路図ノート")
    assert DIARIES_HEADER not in probe_compiled
    assert sup_fact["fact_id"] in session.turn_compiled_prompt.supplement_fact_ids
    base_tok = pc_mod.prompt_compiler.token_counter(probe_compiled)
    sup_tok = pc_mod.prompt_compiler.token_counter(session.turn_compiled_prompt.supplement_text)
    monkeypatch.setattr(pc_mod.prompt_compiler, "input_budget", base_tok + sup_tok + 25)
    monkeypatch.setattr(
        type(provider_registry),
        "snapshot",
        staticmethod(
            lambda pid, mid=None, **_kw: ProviderSnapshot(
                provider_id=pid,
                model_id=mid or "tight-window",
                capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT})),
                adapter=trim_adapter,
                credential_required=False,
                context_window=base_tok + 2600,
                max_output_tokens=512,
            )
        ),
    )
    calls.update(sup=0, mem=0, res=0, fam=0)

    q7: asyncio.Queue = asyncio.Queue()
    await chat_ws.process_queued_turn(
        session,
        "今の短い質問 古い回路図ノート",
        q7,
        session.current_epoch,
        session.history_epoch,
    )
    if session.background_tasks:
        await asyncio.gather(*list(session.background_tasks))
    assert calls == {"sup": 1, "mem": 1, "res": 1, "fam": 1}
    sent_system = captured[-1]["messages"][0]["content"]
    assert DIARIES_HEADER in sent_system and diary_text in sent_system
    assert session.turn_compiled_prompt.supplement_fact_ids == ()
    events_out = [q7.get_nowait()[1] for _ in range(q7.qsize())]
    assert any(e.get("type") == "turn.completed" for e in events_out)
    assert [e for e in events_out if e.get("type") == "memory.referenced"] == []
    refs = await referenced_items_for_turn(
        (session_id, "steins_gate", "self"), session.turn_compiled_prompt, ()
    )
    assert refs == []

    session.history = [
        {"id": mid, "role": "user" if idx % 2 == 0 else "assistant", "content": "後半"}
        for idx, mid in enumerate(late_ids)
    ]
    await delete_diary(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        diary_id=diary_id,
    )
    assert DIARIES_HEADER not in await compile_for_session(session, "次のターン")


@pytest.mark.asyncio
async def test_8_cross_window_self_familiarity_counts_worldline_utterances_and_db_preferences():
    session_id, cid1 = await _new_conv("s8")
    cid2 = (await conversation_service.create(session_id, "steins_gate", identity_mode="self"))[
        "id"
    ]

    async def run_turn(cid: str, text: str) -> str:
        s = chat_ws.SessionState(session_id)
        s.worldline = "steins_gate"
        s.identity_mode = "self"
        s.conversation_id = cid
        await s.reload_history()
        s.append_message({"role": "user", "content": text})
        prompt = await compile_for_session(s, text)
        uid = await models.save_message(
            session_id,
            "user",
            text,
            worldline="steins_gate",
            conversation_id=cid,
        )
        s.set_last_message_id("user", int(uid))
        await models.save_message(
            session_id,
            "assistant",
            "[EMO:neutral] 返事です。",
            worldline="steins_gate",
            conversation_id=cid,
        )
        return prompt

    for i in range(1, 4):
        assert "対等な「です・ます」で話す。" in await run_turn(cid1, f"窓1の発言{i}")
    assert "対等な「です・ます」で話す。" in await run_turn(cid2, "窓2の最初の発言")
    assert "対等な「です・ます」で話す。" in await run_turn(cid1, "窓1の4回目の発言")
    assert "自然な友人の口調で話す。" in await run_turn(cid2, "窓2の2回目・累計6句目")

    s_check = chat_ws.SessionState(session_id)
    s_check.worldline = "steins_gate"
    s_check.identity_mode = "self"
    s_check.conversation_id = cid1
    await s_check.reload_history()
    s_check.append_message({"role": "user", "content": "窓1での確認"})
    assert "自然な友人の口調で話す。" in await compile_for_session(s_check, "窓1での確認")

    pref_session = f"s8-pref-{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=pref_session,
        worldline="steins_gate",
        identity_mode="self",
        display_text="叫我阿哲就行",
        semantic_json={"subject": "user", "predicate": "call_name", "object": "阿哲"},
        confidence=0.86,
    )
    for idx in range(35):
        await create_stable_fact(
            session_id=pref_session,
            worldline="steins_gate",
            identity_mode="self",
            display_text=f"常駐優先事実{idx:02d}：コーヒーを飲む",
            semantic_json={"subject": "user", "predicate": "habit", "object": f"coffee_{idx}"},
            confidence=0.99,
        )
    pref_prompt = await compile_for_session(
        SimpleNamespace(
            session_id=pref_session,
            worldline="steins_gate",
            identity_mode="self",
            base_system_prompt=None,
            system_prompt=None,
            memory_summary="",
            history=[],
            self_name="",
        ),
        "こんにちは",
    )
    assert "叫我阿哲就行" not in pref_prompt
    assert "自然な友人の口調で話す。" in pref_prompt

    neg_session = f"s8-neg-{uuid4().hex[:8]}"
    neg_texts = [
        "老板经常叫我加班",
        "妈妈每天叫我早点睡",
        "用户不喜欢别人不用敬语跟他说话",
        "同僚に呼び捨てにされるのが嫌い",
        "上司に敬語を使わないと怒られる",
        "用户在学日语的平语和敬语区别",
    ]
    for idx, text in enumerate(neg_texts):
        await create_stable_fact(
            session_id=neg_session,
            worldline="steins_gate",
            identity_mode="self",
            display_text=text,
            semantic_json={"subject": "user", "predicate": "note", "object": f"neg_{idx}"},
            confidence=0.92,
        )
    neg_state = chat_ws.SessionState(neg_session)
    neg_state.worldline, neg_state.identity_mode = "steins_gate", "self"
    neg_prompt = await compile_for_session(neg_state, "こんにちは")
    assert "対等な「です・ます」で話す。" in neg_prompt and neg_prompt.count("<example>") == 2


@pytest.mark.asyncio
async def test_9_non_json_model_skips_diary_while_chat_succeeds(monkeypatch):
    from app.services.memory_v11 import diary_generation

    await init_db()
    session_id = f"s9-{uuid4().hex[:8]}"
    credentials.credential_store.set("custom", "no-json-needed-credential")
    sync_bounds = []

    async def record_sync_bound(**kwargs):
        sync_bounds.append(kwargs.get("through_message_id"))
        return await sync_conversation_diary_windows(**kwargs)

    monkeypatch.setattr(diary_generation, "sync_conversation_diary_windows", record_sync_bound)

    def handle_plain_chat(req: httpx.Request) -> httpx.Response:
        if str(req.url).endswith("/chat/completions") and json.loads(req.content).get("stream"):
            sse = (
                'data: {"choices":[{"delta":{"content":"[EMO:neutral] 普通の会話は問題ないわ。"}}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(400, json={"error": "json_object unsupported"})

    no_json = OpenAICompatibleAdapter(
        provider_id="custom",
        base_url="http://localhost:18081/v1",
        default_model="plain-chat-only",
        message_builder=deepseek_service.build_persona_messages,
        credential_required=False,
        transport=httpx.MockTransport(handle_plain_chat),
    )
    monkeypatch.setattr(
        type(provider_registry),
        "snapshot",
        staticmethod(
            lambda pid, mid=None, **_kw: ProviderSnapshot(
                provider_id=pid,
                model_id=mid or "plain-chat-only",
                capabilities=ProviderCapabilities(
                    tasks=frozenset({ProviderTask.CHAT}),
                    structured_output=False,
                ),
                adapter=no_json,
                credential_required=False,
            )
        ),
    )
    conv = await conversation_service.create_and_select(
        session_id,
        "steins_gate",
        provider_id="custom",
        model_id="plain-chat-only",
        identity_mode="self",
    )
    session = chat_ws.SessionState(session_id)
    queue: asyncio.Queue = asyncio.Queue()
    session.worldline = "steins_gate"
    session.provider_id = "custom"
    session.model = "plain-chat-only"
    session.enable_tts = False
    await session.load_from_db()

    for turn_idx in range(8):
        await chat_ws.process_queued_turn(
            session,
            f"メッセージ{turn_idx}",
            queue,
            session.current_epoch,
            session.history_epoch,
        )
    if session.background_tasks:
        await asyncio.gather(*list(session.background_tasks))

    saved_msgs = await models.get_session_messages(
        session_id,
        "steins_gate",
        conversation_id=conv["id"],
    )
    assert len(saved_msgs) == 16
    assert len(sync_bounds) == 8
    assert set(sync_bounds) == {int(row["id"]) for row in saved_msgs if row["role"] == "assistant"}
    await process_due_jobs_once(lease_owner="w9", worldlines=("steins_gate",), limit=16)
    diaries_payload = await list_diaries(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
    )
    assert diaries_payload["diaries"] == []
    [d_row] = await _sql(
        "steins_gate",
        "memory",
        "SELECT diary_id, state FROM memory_diaries WHERE conversation_id = ?",
        (conv["id"],),
    )
    d_job = await get_memory_job(d_row["diary_id"], worldline="steins_gate")
    assert d_row["state"] == "discarded"
    assert d_job is not None
    assert (d_job["state"], d_job["last_error_code"]) == ("cancelled", "json_unsupported")


@pytest.mark.asyncio
async def test_10_truncate_re_enqueue_avoids_unique_cursor_conflict_and_guards_inflight_truncate():
    session_id, cid = await _new_conv("s10")
    ids_prefix = await _append_turns(
        session_id,
        cid,
        [(f"前段{i}", f"[EMO:neutral] 前段返答{i}です。") for i in range(5)],
        start=T0,
    )
    [d1] = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        now=T0 + timedelta(minutes=10),
    )
    assert await promote_due_diary_windows(
        worldline="steins_gate", now=T0 + timedelta(minutes=45)
    ) == [d1]
    ids_suffix = await _append_turns(
        session_id,
        cid,
        [("截断対象", "[EMO:neutral] 消える返答です。")],
        start=T0 + timedelta(minutes=46),
    )
    app = FastAPI()
    app.include_router(conversations_router)
    with TestClient(app) as client:
        resp = client.post(
            f"/api/conversations/{cid}/truncate",
            params={"session_id": session_id, "worldline": "steins_gate"},
            json={
                "from_message_id": ids_suffix[0],
                "expected_last_message_id": ids_suffix[-1],
            },
        )
        assert resp.status_code == 200
        d1_job = await get_memory_job(d1, worldline="steins_gate")
        assert d1_job is not None and d1_job["state"] == "cancelled"

    [d2] = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid,
        now=T0 + timedelta(minutes=48),
    )
    assert d2 != d1
    assert await promote_due_diary_windows(
        worldline="steins_gate", now=T0 + timedelta(minutes=80)
    ) == [d2]
    await process_due_jobs_once(
        lease_owner="w10",
        worldlines=("steins_gate",),
        now=T0 + timedelta(minutes=80),
        diary_completion=lambda **_kw: asyncio.sleep(
            0,
            result={
                "body_ja": "残った前段だけの穏やかな記録よ。",
                "body_zh": "只属于保留前段的平和记录。",
            },
        ),
    )
    diaries = (
        await list_diaries(
            session_id=session_id,
            identity_mode="self",
            worldline="steins_gate",
        )
    )["diaries"]
    assert len(diaries) == 1
    assert (
        diaries[0]["diary_id"],
        diaries[0]["first_message_id"],
        diaries[0]["last_message_id"],
    ) == (d2, ids_prefix[0], ids_prefix[-1])

    cid2 = (await conversation_service.create(session_id, "steins_gate", identity_mode="self"))[
        "id"
    ]
    ids2 = await _append_turns(
        session_id,
        cid2,
        [(f"進行{i}", f"[EMO:neutral] 進行返答{i}です。") for i in range(8)],
        start=T0,
    )
    [d_inf] = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid2,
        now=T0 + timedelta(minutes=20),
    )
    await process_due_jobs_once(
        lease_owner="w10",
        worldlines=("steins_gate",),
        now=T0 + timedelta(minutes=20),
        diary_completion=lambda **_kw: request_erasure(
            session_id=session_id,
            identity_mode="self",
            worldline="steins_gate",
            conversation_id=cid2,
            action="forget",
            forget_long_term=True,
            cutoff=(ids2[10], ids2[-1]),
        ),
    )
    d_inf_job = await get_memory_job(d_inf, worldline="steins_gate")
    assert d_inf_job is not None and d_inf_job["state"] == "cancelled"
    diaries_mid = await list_diaries(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
    )
    assert [d for d in diaries_mid["diaries"] if d["conversation_id"] == cid2] == []

    await process_due_jobs_once(
        lease_owner="w10",
        worldlines=("steins_gate",),
        now=T0 + timedelta(minutes=90),
        diary_completion=lambda **_kw: asyncio.sleep(
            0,
            result={
                "body_ja": "切り詰め後の前半だけの手記よ。",
                "body_zh": "截断后仅属于前半段的手记。",
            },
        ),
    )
    diaries_after = await list_diaries(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
    )
    kept = [d for d in diaries_after["diaries"] if d["conversation_id"] == cid2]
    assert len(kept) == 1
    assert (kept[0]["first_message_id"], kept[0]["last_message_id"]) == (ids2[0], ids2[9])

    cid3 = (await conversation_service.create(session_id, "steins_gate", identity_mode="self"))[
        "id"
    ]
    ids3 = await _append_turns(
        session_id,
        cid3,
        [(f"境界{i}", f"[EMO:neutral] 境界返答{i}です。") for i in range(4)],
        start=T0,
    )
    [d_cur] = await sync_conversation_diary_windows(
        session_id=session_id,
        identity_mode="self",
        worldline="steins_gate",
        conversation_id=cid3,
        now=T0 + timedelta(minutes=10),
    )
    await process_due_jobs_once(
        lease_owner="w10",
        worldlines=("steins_gate",),
        now=T0 + timedelta(minutes=45),
        diary_completion=lambda **_kw: _cursor_bump_and_return(session_id, cid3, ids3[-1] + 1),
    )
    cur_job = await get_memory_job(d_cur, worldline="steins_gate")
    assert cur_job is not None
    assert (cur_job["state"], cur_job["last_error_code"]) == ("cancelled", "conversation_truncated")


@pytest.mark.parametrize(
    ("sources", "ja", "zh", "error"),
    [
        (["喝了三杯茶，开了五次会"], "五月三日に話したわ。", "聊过天。", "ungrounded_date"),
        (["喝了三杯茶，开了五次会"], "話したわ。", "五月三日聊过。", "ungrounded_date"),
        (["五月四日", "六月三日"], "五月三日に話したわ。", "聊过天。", "ungrounded_date"),
        (["我们5/3聊过"], "五月三日に話したわ。", "5月3日聊过。", None),
        (["五月三日聊过"], "5月3日に話したわ。", "5/3聊过。", None),
        (["2026-05-03聊过"], "五月三日に話したわ。", "2026年5月3日聊过。", None),
        (["五月三日聊过"], "五月三日に話したわ。", "2026年5月3日聊过。", "ungrounded_date"),
        (["晚点再聊"], "一時に話すのね。", "晚点聊。", "ungrounded_date"),
        (["晚点再聊"], "また話すのね。", "一点聊。", "ungrounded_date"),
        (["表达了担心"], "心配していたのね。", "有一点担心，这一点我明白。", None),
        (["一点再聊"], "一時に話すのね。", "1:00再聊。", None),
        (["下午一点半见"], "午後一時半に会うのね。", "13:30见。", None),
        (["一点出发，开了三十分钟"], "一時半に出たのね。", "一点半出发。", "ungrounded_date"),
        (["十点再聊"], "一時に話すのね。", "晚点聊。", "ungrounded_date"),
        (["晚点再聊"], "また話すのね。", "星期四再聊。", "ungrounded_date"),
        (["周四再聊"], "木曜日にまた話すのね。", "星期四再聊。", None),
        (["星期五再聊"], "金曜日にまた話すのね。", "周四再聊。", "ungrounded_date"),
        (["先做", "完再说"], "「做完」と言ったわ。", "说了先做完。", "ungrounded_quote"),
        (["先做", "完再说"], "先にするのね。", "说过「做完」。", "ungrounded_quote"),
        (["先做完再说"], "「先做完」と言ったわ。", "说了「先做完」。", None),
        (["喝了两碗茶"], "お茶を二杯飲んだのね。", "喝了两杯茶。", None),
        (["跑了三次"], "三回走ったのね。", "跑了三次。", None),
        (["提到了三件事"], "三年待ったそうね。", "等了三年。", None),
        (["今天和高校老师聊了研究"], "大学教授と研究の話をしたのね。", "聊了研究。", None),
        (["今天和高校老师聊了研究"], "真帆先生と話したのね。", "聊了研究。", "ungrounded_entity"),
    ],
)
def test_diary_grounding_checks_complete_dates_times_and_message_quotes(sources, ja, zh, error):
    assert validate_diary_grounding(ja, zh, [{"text": text} for text in sources])[0] == error


@pytest.mark.asyncio
@pytest.mark.parametrize("eighth_reply_saved", [False, True])
async def test_diary_window_sync_stops_at_triggering_reply(eighth_reply_saved):
    sid, cid = await _new_conv("diary-snapshot")
    scope = dict(
        session_id=sid, conversation_id=cid, worldline="steins_gate", identity_mode="self",
    )
    ids = await _append_turns(sid, cid, [("話そう。", "聞いているわ。")] * 7)
    user8 = await models.save_message(
        sid, "user", "続きがあるよ。", worldline="steins_gate", conversation_id=cid,
    )

    async def save_reply():
        return await models.save_message(
            sid, "assistant", "続きも聞いたわ。", worldline="steins_gate", conversation_id=cid,
        )

    reply8 = await save_reply() if eighth_reply_saved else None
    now = datetime.now(timezone.utc)
    [diary_id] = await sync_conversation_diary_windows(**scope, through_message_id=ids[-1], now=now)
    [row] = await _sql(
        "steins_gate", "memory", "SELECT last_message_id FROM memory_diaries WHERE diary_id=?",
        (diary_id,),
    )
    assert row["last_message_id"] == ids[-1]
    assert await promote_due_diary_windows(worldline="steins_gate", now=now) == []

    if reply8 is None:
        reply8 = await save_reply()
    [updated_id] = await sync_conversation_diary_windows(
        **scope, through_message_id=reply8, now=now,
    )
    assert updated_id == diary_id
    assert await promote_due_diary_windows(worldline="steins_gate", now=now) == [diary_id]
    received = []

    async def completion(**kwargs):
        received.extend(message["id"] for message in kwargs["messages"])
        return {"body_ja": "話を聞いた記録よ。", "body_zh": "听了你说的话。"}

    job = await process_memory_job(
        job_id=diary_id, worldline="steins_gate", lease_owner="diary", diary_completion=completion,
    )
    assert job["state"] == "completed"
    assert received == [*ids, user8, reply8]


@pytest.mark.asyncio
@pytest.mark.parametrize("second_attempt_written", [False, True])
async def test_diary_lease_recovery_never_starts_third_generation(second_attempt_written):
    sid, cid = await _new_conv("diary-restart")
    await _append_turns(sid, cid, [("話そう。", "聞いているわ。")] * 8)
    scope = dict(session_id=sid, identity_mode="self", worldline="steins_gate")
    [diary_id] = await sync_conversation_diary_windows(
        **scope, conversation_id=cid, now=T0 + timedelta(hours=1),
    )
    assert await promote_due_diary_windows(
        worldline="steins_gate", now=T0 + timedelta(hours=1),
    ) == [diary_id]
    calls = []

    for attempt in (1, 2):
        owner = f"worker-{attempt}"

        async def stopped_completion(**_):
            calls.append(attempt)
            if attempt == 2 and second_attempt_written:
                assert await commit_diary(
                    **scope, diary_id=diary_id, lease_owner=owner, verified_phrases=[],
                    body_ja="話を聞いた記録よ。", body_zh="听了你说的话。",
                )
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await process_memory_job(
                job_id=diary_id, worldline="steins_gate", lease_owner=owner,
                diary_completion=stopped_completion,
            )
        await _sql(
            "steins_gate", "memory",
            "UPDATE memory_ingest_jobs SET lease_expires_at=? WHERE job_id=?",
            (T0.isoformat(), diary_id),
        )

    async def unexpected_completion(**_):
        calls.append(3)
        return {"body_ja": "話を聞いた記録よ。", "body_zh": "听了你说的话。"}

    job = await process_memory_job(
        job_id=diary_id, worldline="steins_gate", lease_owner="restarted",
        diary_completion=unexpected_completion,
    )
    assert calls == [1, 2]
    assert job["state"] == ("completed" if second_attempt_written else "failed")
    assert len((await list_diaries(**scope))["diaries"]) == int(second_attempt_written)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fact", "familiar"),
    [
        ("用户在学日语，课文里写着「タメ口でいい」", False),
        ("老师说“请叫我阿哲”。", False),
        ("老板经常叫我加班", False),
        ("用户不喜欢别人不用敬语跟他说话", False),
        ("用户不喜欢别人叫自己小王。请叫我阿哲。", True),
        ("不用敬语。之前老板和我谈过日语学习。", True),
        ("请叫我阿哲", True),
        ("叫我「阿哲」", True),
    ],
)
async def test_self_familiarity_uses_direct_requests_in_saved_facts(fact, familiar):
    sid, cid = await _new_conv("diary-preference")
    await _append_turns(sid, cid, [("こんにちは。", "こんにちは。")])
    await create_stable_fact(
        session_id=sid, worldline="steins_gate", identity_mode="self", display_text=fact,
        semantic_json={"subject": "user", "predicate": "note", "object": "register"},
        confidence=0.94,
    )
    await request_erasure(
        session_id=sid, conversation_id=cid, worldline="steins_gate", identity_mode="self",
        action="forget", forget_long_term=False,
    )
    fresh = await conversation_service.create(sid, "steins_gate", identity_mode="self")
    session = chat_ws.SessionState(sid)
    session.worldline, session.identity_mode, session.conversation_id = (
        "steins_gate", "self", fresh["id"],
    )
    prompt = await compile_for_session(session, "こんにちは。")
    assert ("自然な友人の口調で話す。" in prompt) is familiar


async def _cursor_bump_and_return(session_id: str, cid: str, cutoff_id: int) -> dict[str, str]:
    await _sql(
        "steins_gate",
        "memory",
        """INSERT INTO conversation_erasure_requests (
               request_id,
               session_id,
               identity_mode,
               conversation_id,
               action,
               forget_long_term,
               source_message_ids,
               state,
               created_at,
               cutoff_message_id
           ) VALUES (?, ?, ?, ?, 'forget', 1, '[]', 'completed', ?, ?)""",
        (f"v17:{uuid4()}", session_id, "self", cid, T0.isoformat(), cutoff_id),
    )
    return {"body_ja": "境界後の静かな雑談だったわ。", "body_zh": "边界后的安静闲聊。"}
