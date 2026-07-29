"""Q87 Slice B: translation PROMPT-ENVELOPE (system + user source)."""

from __future__ import annotations

import inspect
import json
from unittest.mock import patch

import httpx
import pytest

# Live-style JA source used as envelope regression fixture (not a "model obeys" claim).
LIVE_JA_SOURCE_FIXTURE = (
    "中国語の正式な表記としては『命运石之门』が広く使われているわ。"
    "Amadeusやnullpo、D-Mail、Labmemの話も出るけど、"
    "字幕では用語の表面形だけ揃えて。"
)


def test_glossary_block_contains_eleven_preserve_surface_rules():
    from app.domain.subtitle_glossary_prompt import (
        FIRST_BATCH_PRESERVE_IDS,
        build_translation_glossary_block,
    )

    block = build_translation_glossary_block()
    assert FIRST_BATCH_PRESERVE_IDS == (
        "P01",
        "P04",
        "P05",
        "P06",
        "P07",
        "P09",
        "P10",
        "P11",
        "P13",
        "P14",
        "P19",
    )
    for needle in (
        "Amadeus",
        "nullpo",
        "Dr. Pepper",
        "Reading Steiner",
        "SERN",
        "世界线",
        "STEINS;GATE",
        "凤凰院凶真",
        "D-Mail",
        "@ch",
        "Lab",
        "未来道具研究所",
    ):
        assert needle in block, needle
    # Runtime glossary must not expose internal plan IDs.
    for pid in FIRST_BATCH_PRESERVE_IDS:
        assert pid not in block, pid


def test_glossary_block_has_negative_guards_not_global_replace():
    from app.domain.subtitle_glossary_prompt import build_translation_glossary_block

    block = build_translation_glossary_block()
    assert "空指针" in block
    assert "禁止" in block or "不得" in block
    assert "无条件" in block or "不得将" in block or "禁止把" in block
    assert "全局" in block or "普通实验室" in block
    assert "字符串" in block or "替换清单" in block or "全局替换" in block
    assert "剧情" in block or "人格" in block or "事实" in block


def test_glossary_block_p19_is_context_only_for_lab():
    from app.domain.subtitle_glossary_prompt import build_translation_glossary_block

    block = build_translation_glossary_block()
    assert "未来道具研究所" in block
    assert "普通实验室" in block or "其他组织" in block


def test_p10_title_dual_form_is_mandatory_not_optional():
    from app.domain.subtitle_glossary_prompt import build_translation_glossary_block

    block = build_translation_glossary_block()
    assert "STEINS;GATE（命运石之门）" in block
    assert "作品标题" in block or "标题语境" in block
    for soft in ("可用", "优先", "可选", "可以保留"):
        assert soft not in block, soft


def test_p13_dmail_is_keep_use_not_tendency():
    from app.domain.subtitle_glossary_prompt import build_translation_glossary_block

    block = build_translation_glossary_block()
    assert "D-Mail" in block
    assert "保持" in block
    assert "使用" in block
    assert "倾向" not in block


def test_r08_handle_glossary_bounded_product_form():
    """B2-B R08 prompt-only: @ch handle 栗悟飯とカメハメ波 → 栗悟饭和龟波功."""
    from app.domain.subtitle_glossary_prompt import (
        build_translation_glossary_block,
        build_translation_messages,
        build_translation_system_content,
    )

    block = build_translation_glossary_block()
    system = build_translation_system_content()
    # Product form + JA handle surface + handle bound.
    assert "栗悟饭和龟波功" in block
    assert "栗悟飯とカメハメ波" in block
    assert "@ch" in block
    assert "handle" in block or "ハンドル" in block or "马甲" in block or "handle" in system.lower()
    # Exclude ordinary Kamehameha / joke / move-name application.
    assert "カメハメ波" in block
    assert "不得套用" in block or "不得" in block
    assert "招式" in block or "玩笑" in block or "话题" in block
    # No meta / evidence pollution in runtime glossary or full system.
    for forbidden in (
        "非唯一官方",
        "唯一官方",
        "evidence debt",
        "evidence",
        "debt",
        "project-approved",
        "project approved",
        "E-debt",
    ):
        assert forbidden not in block, forbidden
        assert forbidden not in system, forbidden
    # No plot/persona expansion license.
    assert "不得" in block and ("归属" in block or "剧情" in block or "人格" in block)
    # Envelope: user remains source-only once.
    messages = build_translation_messages("栗悟飯とカメハメ波だわ。")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "栗悟饭和龟波功" in messages[0]["content"]
    assert messages[1]["content"] == "<source>\n栗悟飯とカメハメ波だわ。\n</source>"
    assert messages[1]["content"].count("栗悟飯とカメハメ波だわ。") == 1
    assert "栗悟饭和龟波功" not in messages[1]["content"]
    assert "专名与术语" not in messages[1]["content"]


def test_envelope_is_exactly_system_plus_user():
    from app.domain.subtitle_glossary_prompt import (
        build_translation_glossary_block,
        build_translation_messages,
        build_translation_system_content,
        build_translation_user_source_content,
    )

    messages = build_translation_messages(LIVE_JA_SOURCE_FIXTURE)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[0]["content"] == build_translation_system_content()
    assert messages[1]["content"] == build_translation_user_source_content(
        LIVE_JA_SOURCE_FIXTURE
    )

    system = messages[0]["content"]
    user = messages[1]["content"]
    block = build_translation_glossary_block()
    assert block in system
    assert block not in user
    assert LIVE_JA_SOURCE_FIXTURE in user
    assert user.count(LIVE_JA_SOURCE_FIXTURE) == 1
    assert LIVE_JA_SOURCE_FIXTURE not in system or system.count(
        LIVE_JA_SOURCE_FIXTURE
    ) == 0
    assert "<source>" in user and "</source>" in user
    # User must not carry glossary framing, plan IDs, or translator instructions.
    assert "专名与术语" not in user
    assert "叠字" not in user
    assert "P01" not in user
    assert "P10" not in user
    assert "翻訳の制約" not in user
    assert "字幕翻訳者" not in user
    # System has reliability guards + anti-meta instructions.
    assert "不自然な畳語" in system
    assert "叠字" in system
    assert "好、好、好……" in system
    assert "過度な繰り返し" in system
    assert "吃音" in system
    assert "<source>" in system or "source" in system
    assert "簡体" in system or "中国語" in system
    assert "提供されていない" in system or "原文がない" in system or "主張" in system


def test_system_contains_eleven_surface_forms_and_guards():
    from app.domain.subtitle_glossary_prompt import build_translation_system_content

    system = build_translation_system_content()
    for needle in (
        "Amadeus",
        "nullpo",
        "Dr. Pepper",
        "Reading Steiner",
        "SERN",
        "世界线",
        "STEINS;GATE（命运石之门）",
        "凤凰院凶真",
        "D-Mail",
        "@ch",
        "Lab",
    ):
        assert needle in system, needle
    assert "不自然な畳語" in system
    assert "過度な繰り返し" in system
    assert "吃音" in system


@pytest.mark.asyncio
async def test_deepseek_runtime_payload_equals_envelope_messages():
    from app.domain.subtitle_glossary_prompt import (
        build_translation_glossary_block,
        build_translation_messages,
    )
    from app.services.deepseek import DeepSeekService

    source = LIVE_JA_SOURCE_FIXTURE
    expected = build_translation_messages(source)
    posts = {"n": 0, "payloads": []}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "命运石之门很常见。"}}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, json=None, headers=None):
            posts["n"] += 1
            posts["payloads"].append(json)
            return FakeResponse()

    service = DeepSeekService()
    service.api_key = "test-key"
    with patch("app.services.deepseek.httpx.AsyncClient", FakeClient):
        out = await service.translate_to_zh(source, api_key="test-key")

    assert out == "命运石之门很常见。"
    assert posts["n"] == 1
    payload = posts["payloads"][0]
    assert payload["messages"] == expected
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert build_translation_glossary_block() in payload["messages"][0]["content"]
    assert build_translation_glossary_block() not in payload["messages"][1]["content"]
    assert payload["messages"][1]["content"].count(source) == 1


@pytest.mark.asyncio
async def test_openai_compatible_one_completion_with_envelope_messages():
    from app.domain.subtitle_glossary_prompt import build_translation_messages
    from app.services.provider_adapters import OpenAICompatibleAdapter

    source = LIVE_JA_SOURCE_FIXTURE
    complete_calls = {"n": 0}

    async def fake_complete(self, messages, **kwargs):
        complete_calls["n"] += 1
        assert messages == build_translation_messages(source)
        return "译好。"

    adapter = OpenAICompatibleAdapter(
        provider_id="mock",
        base_url="http://example.invalid",
        default_model="m",
        message_builder=lambda *a, **k: [],
        credential_required=False,
    )
    with patch.object(OpenAICompatibleAdapter, "_complete_text", fake_complete):
        out = await adapter.translate_to_zh(source, api_key="k")
    assert out == "译好。"
    assert complete_calls["n"] == 1


@pytest.mark.asyncio
async def test_openai_responses_translate_keeps_system_and_user_split():
    """Responses API: system → instructions, user → input; not merged/lost."""
    from app.domain.subtitle_glossary_prompt import (
        build_translation_glossary_block,
        build_translation_messages,
        build_translation_system_content,
        build_translation_user_source_content,
    )
    from app.services.provider_adapters import OpenAIResponsesAdapter

    source = LIVE_JA_SOURCE_FIXTURE
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"output_text": "字幕。"},
        )

    adapter = OpenAIResponsesAdapter(
        message_builder=lambda *a, **k: [],
        transport=httpx.MockTransport(handler),
    )
    out = await adapter.translate_to_zh(source, api_key="openai-secret")
    assert out == "字幕。"
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    expected = build_translation_messages(source)
    assert payload["instructions"] == build_translation_system_content()
    assert payload["instructions"] == expected[0]["content"]
    assert build_translation_glossary_block() in payload["instructions"]
    assert len(payload["input"]) == 1
    assert payload["input"][0]["role"] == "user"
    assert payload["input"][0]["content"] == build_translation_user_source_content(
        source
    )
    assert build_translation_glossary_block() not in payload["input"][0]["content"]
    assert source in payload["input"][0]["content"]
    # System text must not be folded into input.
    assert payload["instructions"] not in payload["input"][0]["content"]


@pytest.mark.asyncio
async def test_gemini_translate_keeps_system_and_user_split():
    """Gemini: systemInstruction vs contents user; not merged/lost."""
    from app.domain.subtitle_glossary_prompt import (
        build_translation_glossary_block,
        build_translation_system_content,
        build_translation_user_source_content,
    )
    from app.services.provider_adapters import GeminiAdapter

    source = LIVE_JA_SOURCE_FIXTURE
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "字幕。"}]}}
                ]
            },
        )

    adapter = GeminiAdapter(
        message_builder=lambda *a, **k: [],
        transport=httpx.MockTransport(handler),
    )
    out = await adapter.translate_to_zh(source, api_key="gemini-secret")
    assert out == "字幕。"
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    sys_text = payload["systemInstruction"]["parts"][0]["text"]
    assert sys_text == build_translation_system_content()
    assert build_translation_glossary_block() in sys_text
    assert len(payload["contents"]) == 1
    assert payload["contents"][0]["role"] == "user"
    user_text = payload["contents"][0]["parts"][0]["text"]
    assert user_text == build_translation_user_source_content(source)
    assert build_translation_glossary_block() not in user_text
    assert source in user_text
    assert sys_text not in user_text


def test_translation_messages_helper_is_envelope():
    from app.domain.subtitle_glossary_prompt import build_translation_messages
    from app.services.provider_adapters import _translation_messages

    assert _translation_messages("テスト。") == build_translation_messages("テスト。")


def test_no_string_replace_preserve_masquerade_in_prompt_module():
    import app.domain.subtitle_glossary_prompt as mod

    source = inspect.getsource(mod)
    assert ".replace(" not in source
    assert "re.sub" not in source
