from unittest.mock import patch

import pytest

from app.routers.chat_ws import (
    SessionState,
    _is_publishable_japanese,
    get_clean_text_stream,
)


class _SequencedStreamProvider:
    def __init__(self, outputs: list[str]):
        self.outputs = outputs
        self.call_count = 0
        self.prompts: list[str] = []

    async def get_chat_stream(self, **kwargs):
        self.prompts.append(kwargs["system_prompt"])
        output = self.outputs[min(self.call_count, len(self.outputs) - 1)]
        self.call_count += 1
        midpoint = max(1, len(output) // 2)
        yield {"content": output[:midpoint], "tool_calls": None}
        yield {"content": output[midpoint:], "tool_calls": None}


def test_publication_gate_rejects_mixed_chinese_with_only_one_kana():
    assert not _is_publishable_japanese("这就是だ")
    assert _is_publishable_japanese("中国語ではなく、日本語で答えるわ")


@pytest.mark.asyncio
async def test_chinese_only_draft_is_not_published_as_japanese():
    provider = _SequencedStreamProvider(
        [
            "[EMO:intellectual] 现在是2026年7月，搜索结果没有冠军信息。",
            "[EMO:intellectual] 検索結果には、優勝国を確認できる情報がまだないわ。",
        ]
    )
    session = SessionState("language-retry")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    published: list[str] = []

    async def capture(delta: str, _emotion: str) -> None:
        published.append(delta)

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, tool_calls, found = await get_clean_text_stream(
            active_history=[],
            memory_summary="",
            session=session,
            on_text_delta=capture,
        )

    assert provider.call_count == 2
    assert "日本語だけで回答" in provider.prompts[1]
    assert "現在是" not in "".join(published)
    assert "検索結果には" in "".join(published)
    assert text.strip() == "検索結果には、優勝国を確認できる情報がまだないわ。"
    assert emotion == "intellectual"
    assert tool_calls == {}
    assert found is True


@pytest.mark.asyncio
async def test_valid_japanese_keeps_incremental_publication():
    provider = _SequencedStreamProvider(
        ["[EMO:neutral] そうね。検索結果を確認してから答えるわ。"]
    )
    session = SessionState("language-pass")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    assert provider.call_count == 1
    assert "".join(published) == text
    assert text.strip() == "そうね。検索結果を確認してから答えるわ。"
