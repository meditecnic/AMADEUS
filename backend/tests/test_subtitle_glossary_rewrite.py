"""Q87 Slice A: deterministic Chinese-subtitle rewrites (test-first)."""

from __future__ import annotations

import asyncio
import inspect
import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.domain.segments import Segment, SegmentPipeline, TurnIdentity
from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderSnapshot,
    ProviderTask,
)


# ---------------------------------------------------------------------------
# Pure rewrite unit cases
# ---------------------------------------------------------------------------


def _apply(text: str) -> str:
    from app.domain.subtitle_glossary_rewrite import apply_subtitle_glossary_rewrites

    return apply_subtitle_glossary_rewrites(text)


def test_r01_full_five_char_token_only():
    assert _apply("克里斯缇娜来了。") == "克里斯蒂娜来了。"
    assert _apply("缇娜") == "缇娜"
    assert _apply("克里斯") == "克里斯"
    assert _apply("克里斯蒂娜") == "克里斯蒂娜"


def test_r02_full_phrase_only():
    assert _apply("未来装置研究所的门口。") == "未来道具研究所的门口。"
    assert _apply("装置研究") == "装置研究"
    assert _apply("未来道具研究所") == "未来道具研究所"


def test_r03_labmen_case_and_fullwidth_latin_token():
    assert _apply("labmen") == "Labmem"
    assert _apply("LabMen") == "Labmem"
    assert _apply("LABMEN") == "Labmem"
    # Fullwidth Latin letters: ｌａｂｍｅｎ; only the token is NFKC'd.
    fullwidth = "\uff4c\uff41\uff42\uff4d\uff45\uff4e"  # ｌａｂｍｅｎ
    assert _apply(f"我们是{fullwidth}。") == "我们是Labmem。"
    # Build ＬａｂＭｅｎ in fullwidth:
    fw = (
        "\uff2c"  # Ｌ
        "\uff41"  # ａ
        "\uff42"  # ｂ
        "\uff2d"  # Ｍ
        "\uff45"  # ｅ
        "\uff4e"  # ｎ
    )
    assert _apply(fw) == "Labmem"


def test_r03_does_not_touch_lab_member_laboratory_or_longer_words():
    assert _apply("lab member") == "lab member"
    assert _apply("lab members") == "lab members"
    assert _apply("laboratory") == "laboratory"
    assert _apply("labmenxyz") == "labmenxyz"
    assert _apply("xlabmen") == "xlabmen"


def test_r03_full_identifier_boundaries():
    """Independent Latin token only; alnum/underscore (ASCII+fullwidth) bind."""
    assert _apply("labmen123") == "labmen123"
    assert _apply("123labmen") == "123labmen"
    assert _apply("labmen_id") == "labmen_id"
    assert _apply("_labmen") == "_labmen"
    # Fullwidth digit / underscore glued forms must not rewrite.
    assert _apply("labmen\uff11") == "labmen\uff11"  # labmen１
    assert _apply("\uff11labmen") == "\uff11labmen"  # １labmen
    assert _apply("labmen\uff3fid") == "labmen\uff3fid"  # labmen＿id
    assert _apply("\uff3flabmen") == "\uff3flabmen"  # ＿labmen
    # Chinese context is NOT a blocking identifier boundary.
    assert _apply("我们labmen来了") == "我们Labmem来了"


def test_r05_ibn5100_whole_token():
    assert _apply("IBN5100") == "IBN 5100"
    assert _apply("用IBN5100启动") == "用IBN 5100启动"
    assert _apply("IBN 5100") == "IBN 5100"
    assert _apply("XIBN5100") == "XIBN5100"
    assert _apply("IBN5100X") == "IBN5100X"


def test_r05_full_identifier_boundaries():
    assert _apply("IBN5100_foo") == "IBN5100_foo"
    assert _apply("1IBN5100") == "1IBN5100"
    # Fullwidth letter/digit adjacent must block.
    assert _apply("\uff29IBN5100") == "\uff29IBN5100"  # ＩIBN5100
    assert _apply("IBN5100\uff11") == "IBN5100\uff11"  # IBN5100１
    assert _apply("IBN5100\uff3fbar") == "IBN5100\uff3fbar"  # IBN5100＿bar
    assert _apply("\uff11IBN5100") == "\uff11IBN5100"  # １IBN5100


def test_r06_dmail_literal_forms():
    assert _apply("D邮件") == "D-Mail"
    assert _apply("Dメール") == "D-Mail"
    assert _apply("D 邮件") == "D-Mail"
    assert _apply("D メール") == "D-Mail"
    assert _apply("Ｄ邮件") == "D-Mail"
    assert _apply("D\u3000邮件") == "D-Mail"  # ideographic space
    assert _apply("用D邮件发送") == "用D-Mail发送"
    assert _apply("通过D邮件") == "通过D-Mail"


def test_r06_rejects_loose_cross_text():
    assert _apply("D  邮件") == "D  邮件"  # two spaces
    assert _apply("D的邮件") == "D的邮件"
    assert _apply("DX邮件") == "DX邮件"
    assert _apply("普通邮件") == "普通邮件"


_R07_JA_WITH_TERM = "このアトラクタフィールドは収束するわ。"
_R07_JA_VENUE = "会場の外に観光客が集まっているわ。"


def test_r07_rewrites_when_ja_source_has_attractor_field():
    """R07: JA source with アトラクタフィールド + ZH 吸引场 → product form."""
    from app.domain.subtitle_glossary_rewrite import apply_subtitle_glossary_rewrites

    zh = "这个吸引场会收敛。"
    assert (
        apply_subtitle_glossary_rewrites(zh, source=_R07_JA_WITH_TERM)
        == "这个世界线收束范围会收敛。"
    )
    assert (
        apply_subtitle_glossary_rewrites("吸引域收敛了。", source=_R07_JA_WITH_TERM)
        == "世界线收束范围收敛了。"
    )
    # Elongated orthographic variant in source also opens the gate.
    assert (
        apply_subtitle_glossary_rewrites(
            "吸引场稳定了。",
            source="アトラクターフィールドが安定したわ。",
        )
        == "世界线收束范围稳定了。"
    )
    # Idempotent target
    assert (
        apply_subtitle_glossary_rewrites(
            "世界线收束范围",
            source=_R07_JA_WITH_TERM,
        )
        == "世界线收束范围"
    )


def test_r07_fails_closed_without_source_or_without_ja_term():
    """No source / ordinary venue JA → never rewrite ordinary 吸引场* Chinese."""
    from app.domain.subtitle_glossary_rewrite import apply_subtitle_glossary_rewrites

    ordinary_zh = (
        "吸引场景、吸引场所、场内观众、场外游客、场馆合作方、吸引域名、这个吸引场会。"
    )
    # Single-arg (R01–R06 path): R07 must not fire.
    assert apply_subtitle_glossary_rewrites(ordinary_zh) == ordinary_zh
    assert apply_subtitle_glossary_rewrites(ordinary_zh, source=None) == ordinary_zh
    assert apply_subtitle_glossary_rewrites(ordinary_zh, source="") == ordinary_zh
    # Venue-sense JA without attractor-field term.
    assert (
        apply_subtitle_glossary_rewrites(ordinary_zh, source=_R07_JA_VENUE)
        == ordinary_zh
    )
    assert apply_subtitle_glossary_rewrites("吸引场景", source=_R07_JA_VENUE) == "吸引场景"
    assert apply_subtitle_glossary_rewrites("吸引场所", source=_R07_JA_VENUE) == "吸引场所"
    assert apply_subtitle_glossary_rewrites("吸引域名", source=_R07_JA_VENUE) == "吸引域名"
    assert apply_subtitle_glossary_rewrites("场内观众", source=_R07_JA_VENUE) == "场内观众"
    assert apply_subtitle_glossary_rewrites("场外游客", source=_R07_JA_VENUE) == "场外游客"
    assert (
        apply_subtitle_glossary_rewrites("场馆合作方", source=_R07_JA_VENUE)
        == "场馆合作方"
    )


def test_r07_zh_has_attract_field_but_ja_is_ordinary_venue_no_rewrite():
    """Coincidence ZH 吸引场 with venue JA must not rewrite."""
    from app.domain.subtitle_glossary_rewrite import apply_subtitle_glossary_rewrites

    zh = "这个吸引场会很热闹。"
    assert apply_subtitle_glossary_rewrites(zh, source=_R07_JA_VENUE) == zh
    assert (
        apply_subtitle_glossary_rewrites(
            zh,
            source="その会場は観光客で賑わっているわ。",
        )
        == zh
    )


def test_r07_with_source_compatible_with_first_batch():
    from app.domain.subtitle_glossary_rewrite import apply_subtitle_glossary_rewrites

    raw = "克里斯缇娜说这个吸引场和 labmen 用IBN5100发D邮件。"
    expected = "克里斯蒂娜说这个世界线收束范围和 Labmem 用IBN 5100发D-Mail。"
    ja = "クリスティーナはアトラクタフィールドとラボメンについて話したわ。"
    once = apply_subtitle_glossary_rewrites(raw, source=ja)
    assert once == expected
    assert apply_subtitle_glossary_rewrites(once, source=ja) == expected


def test_r06_proper_name_left_right_boundaries():
    assert _apply("XD邮件") == "XD邮件"
    assert _apply("ID邮件") == "ID邮件"
    assert _apply("1D邮件") == "1D邮件"
    assert _apply("_D邮件") == "_D邮件"
    assert _apply("D邮件X") == "D邮件X"
    # Fullwidth Latin/digit glued forms
    assert _apply("\uff38D邮件") == "\uff38D邮件"  # ＸD邮件
    assert _apply("Ｄ邮件\uff21") == "Ｄ邮件\uff21"  # Ｄ邮件Ａ
    assert _apply("\uff11D邮件") == "\uff11D邮件"  # １D邮件
    assert _apply("D邮件\uff11") == "D邮件\uff11"  # D邮件１
    # Chinese left/right must still hit
    assert _apply("用D邮件发送") == "用D-Mail发送"
    assert _apply("通过D邮件") == "通过D-Mail"


def test_multi_rule_same_sentence():
    raw = "克里斯缇娜在未来装置研究所，labmen 用IBN5100发D邮件。"
    expected = "克里斯蒂娜在未来道具研究所，Labmem 用IBN 5100发D-Mail。"
    assert _apply(raw) == expected


def test_idempotent():
    raw = "克里斯缇娜在未来装置研究所，LabMen用IBN5100发D 邮件。"
    once = _apply(raw)
    assert _apply(once) == once
    assert "克里斯缇娜" not in once
    assert "未来装置研究所" not in once


def test_unrelated_fullwidth_punctuation_preserved():
    # Fullwidth period/comma must not be NFKC-collapsed on whole string.
    raw = "克里斯缇娜来了\uff01实验\u3001继续\u3002"  # ！、。
    out = _apply(raw)
    assert out == "克里斯蒂娜来了\uff01实验\u3001继续\u3002"
    assert "\uff01" in out
    assert "\u3001" in out
    assert "\u3002" in out


def test_empty_and_passthrough():
    assert _apply("") == ""
    assert _apply("普通字幕没有专名。") == "普通字幕没有专名。"


def test_rewrite_exception_returns_raw_without_logging_body(monkeypatch, capsys):
    from app.domain import subtitle_glossary_rewrite as mod

    def boom(_text: str, **_kwargs) -> str:
        raise RuntimeError("synthetic")

    monkeypatch.setattr(mod, "_apply_rules", boom)
    raw = "克里斯缇娜秘密内容不应出现在日志"
    assert mod.apply_subtitle_glossary_rewrites(raw) == raw
    assert mod.apply_subtitle_glossary_rewrites(raw, source="アトラクタフィールド") == raw
    captured = capsys.readouterr().out + capsys.readouterr().err
    assert "克里斯缇娜" not in captured
    assert "秘密内容" not in captured
    assert "SubtitleGlossaryRewrite" in captured


# ---------------------------------------------------------------------------
# translate_with_provider mount
# ---------------------------------------------------------------------------


def _translation_snapshot(adapter) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_id="mock-translation",
        model_id="mock-model",
        capabilities=ProviderCapabilities(
            tasks=frozenset({ProviderTask.TRANSLATION}),
        ),
        adapter=adapter,
        credential_required=False,
    )


@pytest.mark.asyncio
async def test_translate_with_provider_applies_rewrites():
    from app.routers.chat_ws import translate_with_provider

    class Adapter:
        async def translate_to_zh(self, text, api_key=None, model=None):
            return "克里斯缇娜和 labmen 去未来装置研究所发D邮件，IBN5100。"

    out = await translate_with_provider(
        _translation_snapshot(Adapter()),
        "ja ignored",
        api_key="k",
    )
    assert out == "克里斯蒂娜和 Labmem 去未来道具研究所发D-Mail，IBN 5100。"


@pytest.mark.asyncio
async def test_translate_with_provider_empty_stays_empty():
    from app.routers.chat_ws import translate_with_provider

    class Adapter:
        async def translate_to_zh(self, text, api_key=None, model=None):
            return ""

    out = await translate_with_provider(
        _translation_snapshot(Adapter()),
        "ja",
        api_key=None,
    )
    assert out == ""


# ---------------------------------------------------------------------------
# Component integration (NOT real WebSocket v2 e2e)
# ---------------------------------------------------------------------------


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    """History/control DB isolation (same pattern as test_conversations)."""
    from app.db import init_db, reset_initialization_cache

    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


@pytest.mark.asyncio
async def test_component_pipeline_ready_and_manual_db_save_normalized(isolated_store):
    """Component integration: SegmentPipeline + hook + manual save_message.

    Does **not** exercise auth/chat.send/processor_loop. See
    ``test_ws_v2_segment_ready_and_persisted_translation_normalized`` for real v2 WS.
    """
    from app import models
    from app.routers.chat_ws import translate_with_provider
    from app.services.conversations import conversation_service

    class Adapter:
        async def translate_to_zh(self, text, api_key=None, model=None):
            return "克里斯缇娜在未来装置研究所，labmen用IBN5100发D邮件。"

    snapshot = _translation_snapshot(Adapter())
    events: list[dict] = []

    async def translate(text: str) -> str:
        return await translate_with_provider(snapshot, text, "k")

    async def synthesize(text: str, emotion: str) -> bytes:
        return b"audio"

    pipeline = SegmentPipeline(
        TurnIdentity("conv-q87", "turn-q87", 1),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
        tts_retries=0,
        translation_retries=0,
    )
    await pipeline.run([Segment(0, "クリスティーナは研究所へ行った。", "neutral")])

    ready = [e for e in events if e.get("type") == "segment.ready"]
    assert len(ready) == 1
    expected = "克里斯蒂娜在未来道具研究所，Labmem用IBN 5100发D-Mail。"
    assert ready[0]["zh"] == expected
    assert ready[0]["ja"] == "クリスティーナは研究所へ行った。"
    assert pipeline.translation_text == expected

    conversation = await conversation_service.create(
        "q87-session", "steins_gate", title="Q87 A"
    )
    await models.save_message(
        "q87-session",
        "assistant",
        f"[EMO:neutral] {ready[0]['ja']}",
        conversation_id=conversation["id"],
        translation=pipeline.translation_text,
    )
    rows = await models.get_session_messages(
        "q87-session", "steins_gate", conversation["id"]
    )
    assert rows[0]["translation"] == expected


# ---------------------------------------------------------------------------
# Real protocol v2 WebSocket e2e (app_client + processor_loop)
# ---------------------------------------------------------------------------


class _Q87MockProvider:
    """Zero external model calls: fixed JA stream + intentional ZH variants."""

    async def get_chat_stream(
        self,
        active_history,
        memory_summary=None,
        api_key=None,
        system_prompt=None,
        temperature=0.7,
        tools=None,
        tool_choice=None,
        model=None,
        reasoning_effort=None,
    ):
        yield {
            "content": "[EMO:neutral] クリスティーナは研究所へ行った。",
            "tool_calls": None,
        }

    async def translate_to_zh(self, text, api_key=None, model=None):
        return "克里斯缇娜在未来装置研究所，labmen用IBN5100发D邮件。"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return old_summary


class _SilentTTS:
    async def synthesize_async(self, text, emotion_tag, sovits_url=None):
        return b""

    def reset_sequence(self, session_id, send_callback):
        return None

    def cancel_pending(self, session_id):
        return None

    async def gather_pending(self, session_id):
        return None


def test_ws_v2_segment_ready_and_persisted_translation_normalized(app_client):
    """True v2 path: auth → chat.send → processor_loop → segment.ready + DB."""
    from app.routers.chat_ws import sessions

    session_id = f"q87-ws-v2-{uuid.uuid4().hex[:12]}"
    expected_zh = "克里斯蒂娜在未来道具研究所，Labmem用IBN 5100发D-Mail。"
    provider = _Q87MockProvider()

    with patch("app.routers.chat_ws.deepseek_service", provider), patch(
        "app.routers.chat_ws.tts_manager", _SilentTTS()
    ):
        with app_client.websocket_connect(
            f"/ws/chat?session_id={session_id}"
        ) as websocket:
            websocket.send_json(
                {
                    "type": "auth",
                    "api_key": "test-key",
                    "client": "desktop",
                    "protocol_version": 2,
                    "enable_tts": False,
                }
            )
            websocket.send_json({"type": "chat.send", "content": "研究所はどう？"})

            responses: list[dict] = []
            while True:
                response = websocket.receive_json()
                responses.append(response)
                if response.get("type") in {"turn.completed", "error", "turn.cancelled"}:
                    break

    assert responses[-1]["type"] == "turn.completed", responses[-1]
    ready = [r for r in responses if r.get("type") == "segment.ready"]
    assert ready, f"no segment.ready in {[r.get('type') for r in responses]}"
    assert ready[0]["zh"] == expected_zh
    assert "克里斯缇娜" not in ready[0]["zh"]
    assert "labmen" not in ready[0]["zh"]
    assert "IBN5100" not in ready[0]["zh"]
    assert "D邮件" not in ready[0]["zh"]

    session = sessions[session_id]
    conversation_id = session.conversation_id
    assert conversation_id

    messages = app_client.get(
        f"/api/conversations/{conversation_id}/messages",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert messages.status_code == 200
    assistant_rows = [m for m in messages.json() if m.get("role") == "assistant"]
    assert assistant_rows
    persisted = assistant_rows[-1].get("translation") or ""
    assert persisted == expected_zh
    assert persisted == ready[0]["zh"]


# ---------------------------------------------------------------------------
# repair + v1 call-chain coverage (not prose-only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_path_via_translate_with_provider_normalizes(isolated_store):
    from app import models
    from app.routers.chat_ws import SessionState, repair_turn_translation, translate_with_provider
    from app.services.conversations import conversation_service

    class Adapter:
        async def translate_to_zh(self, text, api_key=None, model=None):
            return "克里斯缇娜发了D邮件。"

    snapshot = _translation_snapshot(Adapter())
    conversation = await conversation_service.create(
        "q87-repair", "steins_gate", title="Repair"
    )
    message_id = await models.save_message(
        "q87-repair",
        "assistant",
        "[EMO:neutral] 本文。",
        conversation_id=conversation["id"],
        translation="旧异形克里斯缇娜",
    )
    session = SessionState("q87-repair")
    session.conversation_id = conversation["id"]
    session.current_epoch = 3
    queue: asyncio.Queue = asyncio.Queue()

    async def translate(text: str) -> str:
        return await translate_with_provider(snapshot, text, "k")

    repaired = await repair_turn_translation(
        session,
        text="本文。",
        message_id=message_id,
        conversation_id=conversation["id"],
        worldline="steins_gate",
        turn_id="turn-r",
        generation=3,
        send_queue=queue,
        translate=translate,
        timeout=1.0,
    )
    assert repaired == "克里斯蒂娜发了D-Mail。"
    rows = await models.get_session_messages(
        "q87-repair", "steins_gate", conversation["id"]
    )
    assert rows[0]["translation"] == "克里斯蒂娜发了D-Mail。"
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event[1]["type"] == "turn.translation_repaired"
    assert event[1]["content"] == "克里斯蒂娜发了D-Mail。"


def test_v1_and_repair_sites_call_translate_with_provider():
    """Structural: protocol v1 whole-turn and repair schedule use the hook."""
    import app.routers.chat_ws as chat_ws

    source = Path(inspect.getfile(chat_ws)).read_text(encoding="utf-8")
    assert re.search(
        r"protocol_version\s*<\s*2[\s\S]{0,400}?translate_with_provider",
        source,
    ), "v1 whole-turn translation must call translate_with_provider"
    assert "translate_whole_turn" in source
    assert re.search(
        r"async def translate_whole_turn[\s\S]{0,200}?translate_with_provider",
        source,
    ), "repair whole-turn must call translate_with_provider"
    hook_src = inspect.getsource(chat_ws.translate_with_provider)
    assert "apply_subtitle_glossary_rewrites" in hook_src
    assert "apply_first_batch_rewrites" not in hook_src
    # v2 segment + v1 whole-turn + repair all use translate_with_provider, which
    # must pass the original Japanese `text` as R07 source.
    assert "source=text" in hook_src or "source= text" in hook_src


@pytest.mark.asyncio
async def test_translate_with_provider_passes_ja_source_for_r07():
    """Production hook: JA attractor source opens R07; venue JA does not."""
    from app.routers.chat_ws import translate_with_provider

    class Adapter:
        async def translate_to_zh(self, text, api_key=None, model=None):
            return "这个吸引场会收敛。"

    snap = _translation_snapshot(Adapter())
    with_term = await translate_with_provider(
        snap,
        "このアトラクタフィールドは収束するわ。",
        api_key="k",
    )
    assert with_term == "这个世界线收束范围会收敛。"

    venue = await translate_with_provider(
        snap,
        "会場の外に観光客が集まっているわ。",
        api_key="k",
    )
    assert venue == "这个吸引场会收敛。"
