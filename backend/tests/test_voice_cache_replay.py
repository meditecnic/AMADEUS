"""Tests for per-message voice cache, replay synthesis, erasure cleanup, LRU cap, and disk reporting."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import time
import wave
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest
from test_memory_v11_api import isolated_client as isolated_client

from app import db, models
from app.domain.segments import AudioErrorCode, Segment, SegmentPipeline, TurnIdentity
from app.services import backups, voice_cache
from app.services.conversation_erasure import (
    _commit_erasure_memory,
    history_erasure_cursor,
    request_erasure,
    resume_erasures,
)
from app.services.tts_queue import TTSServiceError, tts_manager


def _wav(tag: int, frames: int = 80) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack("<" + "h" * frames, *([tag % 30000] * frames)))
    return buf.getvalue()


def _conv(client, owner: str, worldline: str = "steins_gate") -> str:
    res = client.post(
        "/api/conversations", json={"session_id": owner, "worldline": worldline}
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _save(
    owner: str, cid: str, role: str, content: str, worldline: str = "steins_gate"
) -> int:
    return int(
        asyncio.run(
            models.save_message(
                owner, role, content, worldline=worldline, conversation_id=cid
            )
        )
    )


def _archive(
    owner: str,
    cid: str,
    mid: int,
    wavs: list[bytes],
    worldline: str = "steins_gate",
    cur: int | None = None,
) -> bool:
    c = (
        asyncio.run(history_erasure_cursor(worldline, owner, cid))
        if cur is None
        else cur
    )
    return asyncio.run(
        voice_cache.archive_audio(
            worldline=worldline,
            session_id=owner,
            conversation_id=cid,
            message_id=mid,
            expected_erasure=c,
            segments=wavs,
            source="original",
        )
    )


def test_fully_sent_turn_archives_byte_identical_segments_and_replays_from_cache(
    isolated_client, monkeypatch
):
    client, owner = isolated_client, "vc-ac1"
    cid = _conv(client, owner)
    seg0, seg1, emitted = _wav(111, 120), _wav(222, 160), []

    async def _run() -> int:
        rec = voice_cache.TurnAudioRecorder(foreground=True)

        async def _emit(ev: dict) -> None:
            rec.on_event(ev)
            if ev.get("type") == "segment.audio":
                emitted.append(ev["data"])

        cur = await history_erasure_cursor("steins_gate", owner, cid)
        pipe = SegmentPipeline(
            TurnIdentity(cid, "turn-1", 1),
            emit=_emit,
            translate=AsyncMock(side_effect=lambda t: f"译:{t}"),
            synthesize=AsyncMock(
                side_effect=lambda t, _e: seg0 if "一つ目" in t else seg1
            ),
        )
        await pipe.run(
            [
                Segment(0, "一つ目の確認よ。", "neutral"),
                Segment(1, "二つ目も問題ないわ。", "intellectual"),
            ]
        )
        mid = int(
            await models.save_message(
                owner,
                "assistant",
                "[EMO:intellectual] 一つ目の確認よ。二つ目も問題ないわ。",
                conversation_id=cid,
            )
        )
        assert await voice_cache.archive_audio(
            worldline="steins_gate",
            session_id=owner,
            conversation_id=cid,
            message_id=mid,
            expected_erasure=cur,
            segments=rec.complete_segments(
                voice_stopped=pipe._voice_stopped, tts_enabled=True
            ),
            source="original",
        )
        return mid

    mid = asyncio.run(_run())
    assert emitted == [seg0, seg1]
    spy = AsyncMock(side_effect=AssertionError("should not re-synthesize"))
    monkeypatch.setattr(tts_manager, "synthesize_replay_async", spy)
    res = client.post(
        f"/api/conversations/{cid}/messages/{mid}/replay", params={"session_id": owner}
    )
    assert (
        res.status_code == 200
        and res.json()["source"] == "original"
        and res.json()["cached"] is True
    )
    assert [base64.b64decode(s) for s in res.json()["segments"]] == emitted
    assert (voice_cache.message_cache_dir(cid, mid) / "000.wav").read_bytes() == seg0
    assert (voice_cache.message_cache_dir(cid, mid) / "001.wav").read_bytes() == seg1
    spy.assert_not_called()


@pytest.mark.parametrize("reason", ["background", "legacy"])
def test_incomplete_or_legacy_turns_synthesize_once_then_hit_cache(
    isolated_client, monkeypatch, reason
):
    client, owner = isolated_client, f"vc-ac2-{reason}"
    cid = _conv(client, owner)
    mid = _save(
        owner,
        cid,
        "assistant",
        "[EMO:tsundere] べ、別に心配してるわけじゃないんだから。",
    )
    if reason == "background":
        rec = voice_cache.TurnAudioRecorder(foreground=False)
        rec.on_event({"type": "segment.audio", "segment_id": 0, "data": _wav(10)})
        assert rec.complete_segments() == []

    wav, calls = _wav(777, 96), []

    async def fake_synth(text: str, emotion: str, sovits_url: str = None) -> bytes:
        calls.append((text, emotion))
        return wav

    monkeypatch.setattr(tts_manager, "synthesize_replay_async", fake_synth)

    r1 = client.post(
        f"/api/conversations/{cid}/messages/{mid}/replay", params={"session_id": owner}
    )
    assert (
        r1.status_code == 200
        and r1.json()["source"] == "synthesized"
        and r1.json()["cached"] is False
    )
    assert [base64.b64decode(s) for s in r1.json()["segments"]] == [wav] and calls == [
        (calls[0][0], "tsundere")
    ]
    r2 = client.post(
        f"/api/conversations/{cid}/messages/{mid}/replay", params={"session_id": owner}
    )
    assert r2.status_code == 200 and r2.json()["cached"] is True and len(calls) == 1


def test_replay_synthesis_waits_behind_foreground_tts(monkeypatch):
    order, fg_started, release_fg = [], asyncio.Event(), asyncio.Event()

    async def fake_call(
        text: str,
        ref_config: dict,
        sovits_url: str = None,
        quoted_source: str | None = None,
    ) -> bytes:
        if "一つ目" in text:
            order.append("fg_start")
            fg_started.set()
            await release_fg.wait()
            order.append("fg_end")
            return _wav(1)
        order.append("replay_run")
        return _wav(2)

    monkeypatch.setattr(
        tts_manager,
        "emotion_ref_map",
        {"neutral": {"ref_audio_path": "x.wav", "prompt_text": "p"}},
    )
    monkeypatch.setattr(tts_manager, "_async_tts_call", fake_call)

    async def _run():
        fg = asyncio.create_task(
            tts_manager.synthesize_async("一つ目の発話よ。", "neutral")
        )
        await fg_started.wait()
        rp = asyncio.create_task(
            tts_manager.synthesize_replay_async("重播のテストよ。", "neutral")
        )
        await asyncio.sleep(0.05)
        assert order == ["fg_start"]
        release_fg.set()
        await asyncio.gather(fg, rp)

    asyncio.run(_run())
    assert order == ["fg_start", "fg_end", "replay_run"]


def test_archive_and_replay_reject_erased_message_and_leave_no_files(
    isolated_client, monkeypatch
):
    client, owner = isolated_client, "vc-ac3"
    cid = _conv(client, owner)
    u1, a1 = (
        _save(owner, cid, "user", "first"),
        _save(owner, cid, "assistant", "[EMO:neutral] 了解よ。"),
    )
    stale = asyncio.run(history_erasure_cursor("steins_gate", owner, cid))
    assert (
        client.post(
            f"/api/conversations/{cid}/truncate",
            params={"session_id": owner},
            json={"from_message_id": u1, "expected_last_message_id": a1},
        ).status_code
        == 200
    )
    assert _archive(owner, cid, a1, [_wav(5)], cur=stale) is False
    assert not voice_cache.message_cache_dir(cid, a1).exists()

    u2, a2 = (
        _save(owner, cid, "user", "second"),
        _save(owner, cid, "assistant", "[EMO:neutral] まだ消えてないわ。"),
    )

    async def synth_during_truncate(*_a, **_kw) -> bytes:
        await request_erasure(
            session_id=owner,
            identity_mode="okabe",
            worldline="steins_gate",
            conversation_id=cid,
            action="forget",
            forget_long_term=True,
            cutoff=(u2, a2),
        )
        return _wav(9)

    monkeypatch.setattr(tts_manager, "synthesize_replay_async", synth_during_truncate)
    res = client.post(
        f"/api/conversations/{cid}/messages/{a2}/replay", params={"session_id": owner}
    )
    assert res.status_code == 409 and res.json()["detail"]["code"] == "message_erased"
    assert not voice_cache.message_cache_dir(cid, a2).exists()
    assert not list(voice_cache.voice_cache_root().rglob(".tmp-*"))

    # Case C: message survives, but conversation erasure cursor moved -> stale archive rejected.
    _save(owner, cid, "user", "keep")
    a_keep = _save(owner, cid, "assistant", "[EMO:neutral] 残る返事よ。")
    u_cut, a_cut = (
        _save(owner, cid, "user", "cut"),
        _save(owner, cid, "assistant", "[EMO:neutral] 後で消える返事よ。"),
    )
    cur_before = asyncio.run(history_erasure_cursor("steins_gate", owner, cid))
    assert (
        client.post(
            f"/api/conversations/{cid}/truncate",
            params={"session_id": owner},
            json={"from_message_id": u_cut, "expected_last_message_id": a_cut},
        ).status_code
        == 200
    )
    assert _archive(owner, cid, a_keep, [_wav(15)], cur=cur_before) is False
    assert not voice_cache.message_cache_dir(cid, a_keep).exists()


def test_truncate_forget_delete_resume_and_startup_orphan_cleanup(isolated_client):
    client, owner = isolated_client, "vc-ac4"
    c1 = _conv(client, owner)
    _save(owner, c1, "user", "keep")
    a_keep = _save(owner, c1, "assistant", "[EMO:neutral] 残すわ。")
    u_cut, a_cut = (
        _save(owner, c1, "user", "cut"),
        _save(owner, c1, "assistant", "[EMO:neutral] 消すわ。"),
    )
    assert _archive(owner, c1, a_keep, [_wav(1)]) and _archive(
        owner, c1, a_cut, [_wav(2)]
    )
    assert (
        client.post(
            f"/api/conversations/{c1}/truncate",
            params={"session_id": owner},
            json={"from_message_id": u_cut, "expected_last_message_id": a_cut},
        ).status_code
        == 200
    )
    assert (
        voice_cache.message_cache_dir(c1, a_keep).is_dir()
        and not voice_cache.message_cache_dir(c1, a_cut).exists()
    )

    assert (
        client.post(
            f"/api/conversations/{c1}/forget",
            params={"session_id": owner},
            json={"forget_long_term": False},
        ).status_code
        == 200
    )
    assert not voice_cache.message_cache_dir(c1, a_keep).exists()

    c2 = _conv(client, owner)
    _save(owner, c2, "user", "bye")
    a2 = _save(owner, c2, "assistant", "[EMO:neutral] さよなら。")
    assert _archive(owner, c2, a2, [_wav(3)])
    assert (
        client.delete(
            f"/api/conversations/{c2}", params={"session_id": owner}
        ).status_code
        == 204
    )
    assert not (voice_cache.voice_cache_root() / c2).exists()

    c3 = _conv(client, owner)
    u3, a3 = (
        _save(owner, c3, "user", "crash"),
        _save(owner, c3, "assistant", "[EMO:neutral] 電源断テスト。"),
    )
    assert _archive(owner, c3, a3, [_wav(4)])
    req = {
        "request_id": "v17:resume-audio-test",
        "session_id": owner,
        "identity_mode": "okabe",
        "conversation_id": c3,
        "action": "forget",
        "forget_long_term": 1,
        "state": "pending",
        "created_at": "2026-11-22T00:00:00+00:00",
        "cutoff_message_id": u3,
        "last_retained_message_id": None,
        "source_message_ids": json.dumps([u3, a3]),
    }
    asyncio.run(_commit_erasure_memory(req, "steins_gate"))
    asyncio.run(
        resume_erasures(worldline="steins_gate", session_id=owner, conversation_id=c3)
    )
    assert not voice_cache.message_cache_dir(c3, a3).exists()

    c_live = _conv(client, owner, "beta")
    a_live = _save(
        owner, c_live, "assistant", "[EMO:neutral] β線では生きてる。", "beta"
    )
    assert _archive(owner, c_live, a_live, [_wav(5)], "beta")
    orphan = voice_cache.voice_cache_root() / "deleted-conv" / "9999"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "000.wav").write_bytes(_wav(6))
    (orphan / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source": "original",
                "segments": ["000.wav"],
                "last_played_at": 1.0,
            }
        ),
        encoding="utf-8",
    )
    tmp = voice_cache.voice_cache_root() / c_live / ".tmp-999-leftover"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "000.wav").write_bytes(_wav(7))
    stats = asyncio.run(voice_cache.cleanup_orphan_voice_cache())
    assert stats["removed_entries"] >= 1 and stats["removed_temp"] >= 1
    assert (
        not orphan.exists()
        and not tmp.exists()
        and voice_cache.message_cache_dir(c_live, a_live).is_dir()
    )


def test_lru_eviction_over_cap_and_resynthesize_evicted(isolated_client, monkeypatch):
    client, owner = isolated_client, "vc-ac5"
    cid = _conv(client, owner)
    m1 = _save(owner, cid, "assistant", "[EMO:neutral] 一番古い音声よ。")
    m2 = _save(owner, cid, "assistant", "[EMO:neutral] 二番目の音声よ。")
    m3 = _save(owner, cid, "assistant", "[EMO:neutral] 三番目の音声よ。")
    wav = _wav(42, 400)
    monkeypatch.setattr(
        voice_cache, "MAX_VOICE_CACHE_BYTES", (len(wav) + 120) * 2 + 120
    )
    assert _archive(owner, cid, m1, [wav])
    time.sleep(0.02)
    assert _archive(owner, cid, m2, [wav])
    time.sleep(0.02)
    assert (
        client.post(
            f"/api/conversations/{cid}/messages/{m1}/replay",
            params={"session_id": owner},
        ).json()["cached"]
        is True
    )
    time.sleep(0.02)
    assert _archive(owner, cid, m3, [wav])
    assert (
        voice_cache.message_cache_dir(cid, m1).is_dir()
        and not voice_cache.message_cache_dir(cid, m2).exists()
        and voice_cache.message_cache_dir(cid, m3).is_dir()
    )

    spy = AsyncMock(return_value=_wav(99, 100))
    monkeypatch.setattr(tts_manager, "synthesize_replay_async", spy)
    r2 = client.post(
        f"/api/conversations/{cid}/messages/{m2}/replay", params={"session_id": owner}
    )
    assert (
        r2.status_code == 200
        and r2.json()["source"] == "synthesized"
        and r2.json()["cached"] is False
    )
    spy.assert_awaited_once()


def test_disk_usage_lists_voice_cache_and_export_backup_exclude_it(isolated_client):
    client, owner = isolated_client, "vc-ac6"
    cid = _conv(client, owner)
    m1 = _save(owner, cid, "assistant", "[EMO:neutral] 容量テストよ。")
    before = client.get("/api/data/disk").json()["categories"]
    wav = _wav(88, 200)
    assert _archive(owner, cid, m1, [wav])
    cats = client.get("/api/data/disk").json()["categories"]
    assert cats["voice_cache"] >= len(wav) and cats["other"] == before["other"]
    backup = backups.create_backup("manual")
    assert all("voice_cache" not in item["path"] for item in backup["files"])
    assert not any(
        "voice_cache" in p.as_posix()
        for p in (db.get_data_root() / "backups" / backup["id"]).rglob("*")
    )
    for kind in ("conversations", "memory"):
        with ZipFile(
            BytesIO(
                client.get(
                    f"/api/data/export?kind={kind}&format=json&worldline=all"
                ).content
            )
        ) as zf:
            assert zf.namelist() and all(
                "voice_cache" not in n and not n.endswith(".wav") for n in zf.namelist()
            )


def test_replay_reports_tts_unavailable_cleanly(isolated_client, monkeypatch):
    client, owner = isolated_client, "vc-ac9"
    cid = _conv(client, owner)
    m1 = _save(
        owner, cid, "assistant", "[EMO:neutral] 音声サーバーが落ちてる時のテストよ。"
    )
    monkeypatch.setattr(
        tts_manager,
        "synthesize_replay_async",
        AsyncMock(side_effect=TTSServiceError(AudioErrorCode.SERVICE_UNAVAILABLE)),
    )
    res = client.post(
        f"/api/conversations/{cid}/messages/{m1}/replay", params={"session_id": owner}
    )
    assert res.status_code == 503 and res.json()["detail"]["code"] == "tts_unavailable"
    assert not voice_cache.message_cache_dir(cid, m1).exists()
    assert (
        len(
            client.get(
                f"/api/conversations/{cid}/messages", params={"session_id": owner}
            ).json()
        )
        == 1
    )


@pytest.mark.parametrize("mode", ["fully_sent", "voice_stopped", "tts_disabled"])
def test_chat_ws_archives_only_fully_sent_audio(
    isolated_client, isolated_provider_credentials, monkeypatch, mode
):
    from test_emotion_per_conversation import _ScriptedDeepSeek
    from app.routers import chat_ws
    from app.services.conversations import conversation_service
    from app.services.memory_v11 import jobs

    client, owner = isolated_client, f"vc-ws-{mode}"
    isolated_provider_credentials.set("deepseek", "test-credential")
    cid = _conv(client, owner)
    monkeypatch.setattr(jobs, "process_due_jobs_once", AsyncMock())
    monkeypatch.setattr(
        conversation_service, "schedule_auto_title", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        chat_ws,
        "deepseek_service",
        _ScriptedDeepSeek(
            ["[EMO:intellectual] 実験の結果は良好よ。次へ進みましょう。"]
        ),
    )
    second_started = Event()
    wavs = [_wav(301, 100), _wav(302, 140)]
    synth_calls = []

    async def fake_fg(*_a, **_kw) -> bytes:
        i = len(synth_calls)
        synth_calls.append(i)
        if i == 1 and mode == "voice_stopped":
            second_started.set()
            await asyncio.Future()
        return wavs[i]

    complete_segments = voice_cache.TurnAudioRecorder.complete_segments
    recorded_stops = []

    def record_gate(recorder, *, voice_stopped, tts_enabled):
        recorded_stops.append(voice_stopped)
        return complete_segments(
            recorder, voice_stopped=voice_stopped, tts_enabled=tts_enabled
        )

    monkeypatch.setattr(voice_cache.TurnAudioRecorder, "complete_segments", record_gate)
    monkeypatch.setattr(tts_manager, "synthesize_async", fake_fg)
    monkeypatch.setattr(
        tts_manager,
        "synthesize_replay_async",
        AsyncMock(side_effect=AssertionError("must use cache")),
    )

    emitted: list[tuple[int, bytes]] = []
    try:
        with client.websocket_connect(f"/ws/chat?session_id={owner}") as ws:
            ws.send_json(
                {
                    "type": "auth",
                    "protocol_version": 2,
                    "client": "desktop",
                    "enable_tts": mode != "tts_disabled",
                    "conversation_mode": "history",
                    "conversation_id": cid,
                }
            )
            assert ws.receive_json()["type"] == "session.ready"
            ws.send_json({"type": "chat.send", "content": "实验怎么样了"})
            for _ in range(40):
                frame = ws.receive_json()
                assert frame["type"] != "error", frame
                if frame["type"] == "segment.audio":
                    emitted.append(
                        (int(frame["segment_id"]), base64.b64decode(frame["data"]))
                    )
                    if mode == "voice_stopped":
                        assert second_started.wait(5)
                        ws.send_json({"type": "voice.stop"})
                if frame["type"] == "turn.completed":
                    if mode == "fully_sent":
                        ws.send_json({"type": "voice.stop"})
                        for _ in range(10):
                            if ws.receive_json().get("state") == "voice_stopped":
                                break
                        else:
                            pytest.fail("voice.stop was not acknowledged")
                    break
            else:
                pytest.fail("turn did not complete")
    finally:
        chat_ws.sessions.pop(owner, None)

    expected = [data for _, data in sorted(emitted, key=lambda item: item[0])]
    if mode != "fully_sent":
        assert not (voice_cache.voice_cache_root() / cid).exists()
        if mode == "voice_stopped":
            assert expected == wavs[:1]
            assert recorded_stops == [True]
        else:
            assert not expected and not synth_calls
        return

    assert expected == wavs
    assistant_row = [
        r
        for r in client.get(
            f"/api/conversations/{cid}/messages", params={"session_id": owner}
        ).json()
        if r["role"] == "assistant"
    ][-1]
    replay = client.post(
        f"/api/conversations/{cid}/messages/{assistant_row['id']}/replay",
        params={"session_id": owner},
    )
    assert (
        replay.status_code == 200
        and replay.json()["source"] == "original"
        and replay.json()["cached"] is True
    )
    assert [base64.b64decode(s) for s in replay.json()["segments"]] == expected


def test_cancelled_staging_cleans_temporary_wav_before_truncate(
    isolated_client, monkeypatch
):
    client, owner = isolated_client, "vc-cancel-stage"
    cid = _conv(client, owner)
    first = _save(owner, cid, "user", "cut")
    reply = _save(owner, cid, "assistant", "Remove this reply.")
    original = voice_cache._stage_entry_dir
    release = Event()

    async def exercise():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def delayed_stage(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return original(*args)

        monkeypatch.setattr(voice_cache, "_stage_entry_dir", delayed_stage)
        task = asyncio.create_task(
            voice_cache.archive_audio(
                worldline="steins_gate",
                session_id=owner,
                conversation_id=cid,
                message_id=reply,
                expected_erasure=0,
                segments=[_wav(10)],
                source="original",
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=0.2)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not done

    asyncio.run(exercise())
    response = client.post(
        f"/api/conversations/{cid}/truncate",
        params={"session_id": owner},
        json={"from_message_id": first, "expected_last_message_id": reply},
    )
    assert response.status_code == 200
    assert not list(voice_cache.voice_cache_root().rglob("*.wav"))


def test_eviction_does_not_block_archive_or_fail_for_disappearing_directory(
    isolated_client, monkeypatch
):
    client, owner = isolated_client, "vc-eviction"
    cid = _conv(client, owner)
    reply = _save(owner, cid, "assistant", "こんにちは。")
    release = Event()
    started = Event()
    enforce = voice_cache.enforce_cache_limit

    def slow_sweep(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return enforce(*args, **kwargs)

    monkeypatch.setattr(voice_cache, "enforce_cache_limit", slow_sweep)

    async def exercise():
        try:
            assert await asyncio.wait_for(
                voice_cache.archive_audio(
                    worldline="steins_gate",
                    session_id=owner,
                    conversation_id=cid,
                    message_id=reply,
                    expected_erasure=0,
                    segments=[_wav(11)],
                    source="original",
                ),
                timeout=2,
            )
            assert await asyncio.to_thread(started.wait, 2)
            assert voice_cache.message_cache_dir(cid, reply).is_dir()
        finally:
            release.set()

    asyncio.run(exercise())
    original_scan = voice_cache.os.scandir

    def disappearing_scan(path):
        if Path(path) == voice_cache.message_cache_dir(cid, reply):
            raise FileNotFoundError(path)
        return original_scan(path)

    monkeypatch.setattr(voice_cache.os, "scandir", disappearing_scan)
    assert enforce(max_bytes=0) == 0


def test_cache_sweeps_and_erasure_skip_windows_junction(isolated_client):
    winapi = pytest.importorskip("_winapi")

    client, owner = isolated_client, "vc-junction"
    cid = _conv(client, owner)
    first = _save(owner, cid, "user", "cut")
    reply = _save(owner, cid, "assistant", "こんにちは。")
    root = voice_cache.voice_cache_root()
    sibling = root.parent / "probe-other-data"
    outside = sibling / str(reply)
    outside.mkdir(parents=True)
    marker = outside / "000.wav"
    marker.write_bytes(_wav(12))
    root.mkdir()
    junction = root / cid
    winapi.CreateJunction(str(sibling), str(junction))
    try:
        assert (
            asyncio.run(voice_cache.cleanup_orphan_voice_cache())["removed_entries"]
            == 0
        )
        assert voice_cache.enforce_cache_limit(max_bytes=0) == 0
        resp = client.post(
            f"/api/conversations/{cid}/truncate",
            params={"session_id": owner},
            json={"from_message_id": first, "expected_last_message_id": reply},
        )
        assert resp.status_code == 200
        assert junction.is_dir() and marker.read_bytes() == _wav(12)
    finally:
        junction.rmdir()


@pytest.mark.parametrize("termination", ["cancel", "error"])
def test_ws_history_saved_follows_interruption_notice_persistence(
    isolated_client, isolated_provider_credentials, monkeypatch, termination
):
    from test_emotion_per_conversation import _ScriptedDeepSeek

    from app.routers import chat_ws
    from app.services.conversations import conversation_service
    from app.services.memory_v11 import jobs

    client, owner = isolated_client, f"vc-history-{termination}"
    isolated_provider_credentials.set("deepseek", "test-credential")
    cid = _conv(client, owner)
    monkeypatch.setattr(jobs, "process_due_jobs_once", AsyncMock())
    monkeypatch.setattr(
        conversation_service, "schedule_auto_title", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(
        chat_ws,
        "deepseek_service",
        _ScriptedDeepSeek(["[EMO:neutral] まだ話しているわ。"]),
    )
    release_notice = Event()
    persist_notice = chat_ws._persist_aborted_turn

    async def delayed_notice(**kwargs):
        assert await asyncio.to_thread(release_notice.wait, 8)
        await persist_notice(**kwargs)

    monkeypatch.setattr(chat_ws, "_persist_aborted_turn", delayed_notice)
    save = models.save_message
    fail_assistant = termination == "error"

    async def save_or_fail(session_id, role, content, **kwargs):
        nonlocal fail_assistant
        if role == "assistant" and fail_assistant:
            fail_assistant = False
            raise RuntimeError("test persistence error")
        return await save(session_id, role, content, **kwargs)

    monkeypatch.setattr(models, "save_message", save_or_fail)

    async def pending_audio(*_a, **_kw):
        await asyncio.Future()

    monkeypatch.setattr(tts_manager, "synthesize_async", pending_audio)
    history_url = f"/api/conversations/{cid}/messages"

    try:
        with client.websocket_connect(f"/ws/chat?session_id={owner}") as ws:
            try:
                ws.send_json(
                    {
                        "type": "auth",
                        "protocol_version": 2,
                        "client": "desktop",
                        "enable_tts": termination == "cancel",
                        "conversation_mode": "history",
                        "conversation_id": cid,
                    }
                )
                assert ws.receive_json()["type"] == "session.ready"
                ws.send_json({"type": "chat.send", "content": "停止前の質問"})
                turn_id = None
                for _ in range(40):
                    frame = ws.receive_json()
                    if frame["type"] == "turn.started":
                        turn_id = frame["turn_id"]
                    if termination == "cancel" and frame["type"] == "segment.ready":
                        ws.send_json({"type": "turn.cancel"})
                    if frame["type"] == (
                        "turn.cancelled" if termination == "cancel" else "error"
                    ):
                        break
                else:
                    pytest.fail("missing terminal frame")
                pending_history = client.get(
                    history_url, params={"session_id": owner}
                ).json()
                assert turn_id and [
                    (row["role"], row["turn_id"]) for row in pending_history
                ] == [("user", turn_id)]
                release_notice.set()
                for _ in range(10):
                    frame = ws.receive_json()
                    if frame["type"] == "turn.history_saved":
                        assert (
                            frame["turn_id"] == turn_id
                            and frame["conversation_id"] == cid
                        )
                        break
                else:
                    pytest.fail("missing persisted-history notification")
                rows = client.get(history_url, params={"session_id": owner}).json()
                assert [(row["role"], row["turn_id"]) for row in rows] == [
                    ("user", turn_id),
                    ("assistant", turn_id),
                ]
                cut = client.post(
                    f"/api/conversations/{cid}/truncate",
                    params={"session_id": owner},
                    json={
                        "from_message_id": rows[0]["id"],
                        "expected_last_message_id": rows[1]["id"],
                    },
                )
                assert cut.status_code == 200
            finally:
                release_notice.set()
    finally:
        chat_ws.sessions.pop(owner, None)


def test_remove_tree_logs_winerror_when_present(tmp_path, monkeypatch, caplog):
    target = tmp_path / "000.wav"
    target.write_bytes(_wav(8))

    def raise_sharing_violation(self):
        err = PermissionError("sharing violation")
        err.winerror = 32  # type: ignore[attr-defined]
        raise err

    monkeypatch.setattr(Path, "unlink", raise_sharing_violation)
    with caplog.at_level("WARNING", logger="app.services.voice_cache"):
        assert voice_cache._remove_tree_best_effort(target) is False
    assert "PermissionError, winerror=32" in caplog.text
