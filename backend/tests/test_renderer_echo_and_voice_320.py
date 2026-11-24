"""[320] Offline regression tests for renderer anti-echo, voice register, and quoted Chinese TTS."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.domain.japanese_response_renderer import JAPANESE_RENDERER_SYSTEM_PROMPT
from app.routers import chat_ws
from app.routers.chat_ws import SessionState, get_clean_text_stream, sessions
from app.services.prompt_compiler import CompiledPrompt, PromptInputs
from app.services.soul_engine import EmotionVector
from app.services.tts_queue import TTSQueueManager, tts_speech_surface


class _FakeProvider:
    def __init__(self, draft: str, rendered: str):
        self.draft = draft
        self.rendered = rendered
        self.calls: list[dict] = []

    async def get_chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        output = self.draft if len(self.calls) == 1 else self.rendered
        for char in output:
            yield {"content": char, "tool_calls": None}

    async def translate_to_zh(self, text: str, api_key=None):
        return f"译:{text}"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return old_summary


def _auth(ws, *, enable_tts: bool = True):
    ws.send_json({
        "type": "auth",
        "client": "desktop",
        "protocol_version": 2,
        "enable_tts": enable_tts,
    })
    while True:
        frame = ws.receive_json()
        assert frame["type"] != "error", frame
        if frame["type"] == "session.ready":
            return


def _complete(ws) -> list[dict]:
    events: list[dict] = []
    while True:
        frame = ws.receive_json()
        events.append(frame)
        if frame["type"] in {"turn.completed", "turn.cancelled", "error"}:
            assert frame["type"] == "turn.completed", frame
            return events


def _latest_history_body(session_id: str) -> str:
    content = [
        row["content"]
        for row in sessions[session_id].history
        if row["role"] == "assistant"
    ][-1]
    return content.split("] ", 1)[-1]


def _run_ws_turn(app_client, monkeypatch, *, user_msg: str, draft: str, rendered: str):
    provider = _FakeProvider(draft, rendered)
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    tts_calls: list[tuple[str, str, dict]] = []

    async def fake_synthesize(self, text: str, emotion_tag: str, sovits_url=None, **kwargs) -> bytes:
        tts_calls.append((text, emotion_tag, kwargs))
        return b"RIFF....WAVE"

    monkeypatch.setattr(type(chat_ws.tts_manager), "synthesize_async", fake_synthesize)
    session_id = f"test-320-{uuid4()}"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        _auth(ws, enable_tts=True)
        ws.send_json({"type": "chat.send", "content": user_msg})
        events = _complete(ws)
    ready = [event["ja"] for event in events if event["type"] == "segment.ready"]
    history_body = _latest_history_body(session_id)
    return provider, ready, history_body, tts_calls


def test_320_h09_t8_leading_fragment_isolates_user_request_and_keeps_order_and_tts(
    app_client, monkeypatch
):
    """[319] H09 T8: leading '摸鱼……' renders concisely without echoing the user's Chinese."""
    user_msg = "也是，一个人下没意思。那你平时摸鱼都干嘛"
    tail_ja = (
        "要するに暇つぶしね。うーん、そういうのを正面から聞かれると、言葉に詰まるわね。"
        "気になる論文を拾い読みするとか、その辺の話題を眺めるとか、そういう地味なことよ。"
        "べつに大したことしてないんだから、それ以上掘らないでくれる？"
    )
    draft = f"[EMO:tsundere] 摸鱼……{tail_ja}"
    rendered_head = "サボり、ね……"

    provider, ready, history_body, tts_calls = _run_ws_turn(
        app_client, monkeypatch, user_msg=user_msg, draft=draft, rendered=rendered_head
    )

    assert len(provider.calls) == 2
    renderer_call = provider.calls[1]
    assert renderer_call["system_prompt"] == JAPANESE_RENDERER_SYSTEM_PROMPT
    assert renderer_call["reasoning_effort"] == "low"
    payload = json.loads(renderer_call["active_history"][0]["content"].split("\n", 1)[1])
    assert payload == {"assistant_draft": "摸鱼……"}
    assert "original_user_request" not in renderer_call["active_history"][0]["content"]
    assert "一个人下没意思" not in str(renderer_call["active_history"])
    assert "平时摸鱼都干嘛" not in str(renderer_call["active_history"])
    assert "断片" in renderer_call["active_history"][1]["content"]
    assert "常体" in renderer_call["active_history"][1]["content"]

    expected = rendered_head + tail_ja
    assert "".join(ready) == expected
    assert history_body == expected
    assert "摸鱼" not in expected
    assert [text for text, _, _ in tts_calls][0] == rendered_head
    assert "".join(text for text, _, _ in tts_calls) == expected
    assert all("摸鱼" not in text for text, _, _ in tts_calls)


def test_320_h14_t6_leading_fragment_never_completes_from_user_and_preserves_order_and_tts(
    app_client, monkeypatch
):
    """[319] H14 T6: leading '泡面啊……' stays concise and splices before valid Japanese."""
    user_msg = "……泡面。行了吧，别念了。话说回来，你那边现在几点了？"
    tail_ja = (
        "まあ、何も食べないよりはましね。でもあれだけじゃ足りないわよ、せめて卵でも落としなさい。"
        "私も人のこと言えた立場じゃないけど。こっちは昼を少し過ぎたところ。"
        "あんたのとこも同じくらいの時間でしょう、たぶん。"
    )
    draft = f"[EMO:neutral] 泡面啊……{tail_ja}"
    rendered_head = "カップ麺ね……"

    provider, ready, history_body, tts_calls = _run_ws_turn(
        app_client, monkeypatch, user_msg=user_msg, draft=draft, rendered=rendered_head
    )

    assert len(provider.calls) == 2
    renderer_call = provider.calls[1]
    assert renderer_call["reasoning_effort"] == "low"
    payload = json.loads(renderer_call["active_history"][0]["content"].split("\n", 1)[1])
    assert payload == {"assistant_draft": "泡面啊……"}
    assert "别念了" not in str(renderer_call["active_history"])
    assert "现在几点了" not in str(renderer_call["active_history"])
    assert "断片" in renderer_call["active_history"][1]["content"]

    expected = rendered_head + tail_ja
    assert "".join(ready) == expected
    assert history_body == expected
    assert "泡面" not in expected
    assert [text for text, _, _ in tts_calls][0] == rendered_head
    assert "".join(text for text, _, _ in tts_calls) == expected


def test_320_s02_t1_full_chinese_draft_guides_kurisu_voice_and_low_thinking(
    app_client, monkeypatch
):
    """[319] S02 T1: full Chinese draft gets Kurisu voice guard, low reasoning, and clean TTS."""
    user_msg = "唉……今天真够呛"
    chinese_body = "这个点才回来啊。先别急着细说，喝口热的、坐一会儿再说。今天到底是哪一块最难受？"
    draft = f"[EMO:neutral] {chinese_body}"
    rendered_full = (
        "こんな時間に帰ってきたのね。先に細かい話はいいから、温かいものでも飲んで少し座りなさい。"
        "今日は一体どこが一番つらかったの？"
    )

    provider, ready, history_body, tts_calls = _run_ws_turn(
        app_client, monkeypatch, user_msg=user_msg, draft=draft, rendered=rendered_full
    )

    assert len(provider.calls) == 2
    renderer_call = provider.calls[1]
    assert renderer_call["system_prompt"] == JAPANESE_RENDERER_SYSTEM_PROMPT
    assert renderer_call["reasoning_effort"] == "low"
    payload = json.loads(renderer_call["active_history"][0]["content"].split("\n", 1)[1])
    assert payload == {"assistant_draft": chinese_body}
    assert "今天真够呛" not in str(renderer_call["active_history"])
    control = renderer_call["active_history"][1]["content"]
    assert "断片" not in control
    assert "牧瀬紅莉栖の自然な常体" in control
    for forbidden in ("「〜だよ」", "「〜だね」", "「〜だろ」"):
        assert forbidden in JAPANESE_RENDERER_SYSTEM_PROMPT
    assert "「〜しよう」" not in JAPANESE_RENDERER_SYSTEM_PROMPT

    assert "".join(ready) == rendered_full
    assert history_body == rendered_full
    assert "这个点才回来" not in history_body
    assert "".join(text for text, _, _ in tts_calls) == rendered_full
    assert all("这个点才回来" not in text for text, _, _ in tts_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_mode", "familiarity", "expected_marker"),
    [
        ("okabe", "initial", "牧瀬紅莉栖の自然な常体"),
        ("self", "familiar", "牧瀬紅莉栖の自然な常体"),
        ("self", "initial", "さっぱりと対等な敬体（です・ます。過剰な敬語は不可）"),
    ],
)
async def test_320_renderer_follows_turn_register_plain_vs_polite(
    monkeypatch, identity_mode, familiarity, expected_marker
):
    provider = _FakeProvider("[EMO:neutral] 你今天辛苦了吧。", "今日はお疲れさま。")
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    session = SessionState(f"reg-{uuid4()}")
    session.api_key = "test-key"
    session.identity_mode = identity_mode
    session.turn_compiled_prompt = CompiledPrompt(
        text="prompt",
        inputs=PromptInputs(
            worldline="steins_gate",
            base_identity="",
            emotion=EmotionVector(
                trust=0.5,
                attachment=0.5,
                irritation=0.1,
                anxiety=0.1,
                jealousy=0.1,
                volatility=0.1,
            ),
            episodic=[],
            web_evidence=[],
            working_summary="",
            recent_history=[],
            current_user_message="累了",
            identity_mode=identity_mode,
            familiarity=familiarity,
        ),
    )

    await get_clean_text_stream([{"role": "user", "content": "累了"}], "", session)

    assert len(provider.calls) == 2
    assert expected_marker in provider.calls[1]["active_history"][1]["content"]


@pytest.mark.asyncio
async def test_320_quoted_chinese_tts_boundary_handles_stage2_cases_and_keeps_ui_text(
    app_client, monkeypatch
):
    """Quoted simplified Chinese uses speech-only auto boundaries without altering UI text."""
    tts = TTSQueueManager()
    response = MagicMock(status_code=200, content=b"RIFF....WAVE")
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.aclose = AsyncMock()
    tts.client = client
    ref = {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "参照プロンプト"}

    # H06 T12: user message itself quoted Japanese 「会話が切れれば止まる」, yet 「没发生过」 must still switch to auto.
    h06_user = (
        "过程……行，这个我记下了。不过你刚才那句「会話が切れれば止まる」让我有点在意"
        "——那在你停下来的那段时间里，对你来说算不算「没发生过」？"
    )
    h06_spoken = "正確に言うと、「没发生过」とは少し違うの。"
    await tts._async_tts_call(h06_spoken, ref, quoted_source=h06_user)
    payload = client.post.await_args.kwargs["json"]
    assert payload["text_lang"] == "auto"
    assert payload["text"] == "正確に言うと、「\u200b没发生过\u200b」とは少し違うの。"

    # H12 T7 without quoted_source (v1 queue / voice / prior-turn reference): still auto-bounded.
    h12_spoken = "マグカップを洗ったくらいで「将功补过」って、あんたの物差しはだいぶ甘いわね。"
    await tts._async_tts_call(h12_spoken, ref, quoted_source=None)
    payload = client.post.await_args.kwargs["json"]
    assert payload["text_lang"] == "auto"
    assert "「\u200b将功补过\u200b」" in payload["text"]

    # H14 T8: user used Chinese comma 「，」, reply used Japanese comma 「、」.
    h14_spoken = "ちょっと、今の「算了、不说了」で話を畳まないで。"
    await tts._async_tts_call(h14_spoken, ref, quoted_source="我这边……算了，不说了。")
    payload = client.post.await_args.kwargs["json"]
    assert payload["text_lang"] == "auto"
    assert "「\u200b算了、不说了\u200b」" in payload["text"]

    # S05 T8: mixed Japanese kana + simplified Chinese inside one quote isolates only the Chinese Han run.
    s05_spoken = "「全部が菜鸡互啄」って言い切るのは乱暴よ。"
    await tts._async_tts_call(s05_spoken, ref, quoted_source="会不会全是菜鸡互啄啊")
    payload = client.post.await_args.kwargs["json"]
    assert payload["text_lang"] == "auto"
    assert payload["text"] == "「全部が\u200b菜鸡互啄\u200b」って言い切るのは乱暴よ。"

    # Pure Japanese quote stays strictly text_lang="ja" with zero boundaries.
    surf, lang = tts_speech_surface("その「了解」は余計よ。", "今日は了解した")
    assert lang == "ja"
    assert "\u200b" not in surf

    # End-to-end v2 WS check: UI segment.ready and history keep canonical text without \u200b,
    # while GPT-SoVITS HTTP boundary gets \u200b + text_lang="auto".
    provider = _FakeProvider(f"[EMO:tsundere] {h12_spoken}", "")
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    monkeypatch.setitem(chat_ws.tts_manager.emotion_ref_map, "tsundere", ref)
    monkeypatch.setattr(chat_ws.tts_manager, "client", client)
    client.is_closed = False
    client.post.reset_mock()

    session_id = f"tts-quote-{uuid4()}"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        _auth(ws, enable_tts=True)
        ws.send_json({"type": "chat.send", "content": "我把杯子洗了，算不算将功补过"})
        events = _complete(ws)

    ready = [event["ja"] for event in events if event["type"] == "segment.ready"]
    assert ready == [h12_spoken]
    assert "\u200b" not in ready[0]
    assert _latest_history_body(session_id) == h12_spoken
    ws_payload = client.post.await_args.kwargs["json"]
    assert ws_payload["text_lang"] == "auto"
    assert "「\u200b将功补过\u200b」" in ws_payload["text"]
