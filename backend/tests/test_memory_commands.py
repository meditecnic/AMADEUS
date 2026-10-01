"""Q65-EXPLICIT-MEMORY-COMMAND-01 Slice S1: deterministic intent only (no I/O)."""

from __future__ import annotations

import pytest

from app.services.memory_commands import (
    MemoryCommandIntent,
    classify_memory_command,
)


class TestClassifyRemember:
    def test_payload_chinese_colon(self) -> None:
        r = classify_memory_command("记住：我喜欢黑咖啡")
        assert r.intent == "remember"
        assert r.deixis == "current"
        assert r.payload_text == "我喜欢黑咖啡"
        assert r.error is None

    def test_payload_please_remember_name(self) -> None:
        r = classify_memory_command("请记住我的名字是林然")
        assert r.intent == "remember"
        assert r.deixis == "current"
        assert r.payload_text == "我的名字是林然"
        assert r.error is None

    def test_pure_deixis_chinese(self) -> None:
        r = classify_memory_command("记住这个")
        assert r.intent == "remember"
        assert r.deixis == "previous"
        assert r.payload_text == ""
        assert r.error is None

    def test_pure_deixis_remember_this(self) -> None:
        r = classify_memory_command("remember this")
        assert r.intent == "remember"
        assert r.deixis == "previous"
        assert r.payload_text == ""
        assert r.error is None

    def test_english_remember_that_payload(self) -> None:
        r = classify_memory_command("remember that I prefer dark mode")
        assert r.intent == "remember"
        assert r.deixis == "current"
        assert r.payload_text == "I prefer dark mode"
        assert r.error is None


class TestClassifyForget:
    def test_pure_deixis_chinese(self) -> None:
        r = classify_memory_command("忘掉这件事")
        assert r.intent == "forget"
        assert r.deixis == "previous"
        assert r.payload_text == ""
        assert r.error is None

    def test_pure_deixis_forget_this(self) -> None:
        r = classify_memory_command("forget this")
        assert r.intent == "forget"
        assert r.deixis == "previous"
        assert r.error is None

    def test_payload_forget(self) -> None:
        r = classify_memory_command("忘掉：我喜欢黑咖啡")
        assert r.intent == "forget"
        assert r.deixis == "current"
        assert r.payload_text == "我喜欢黑咖啡"
        assert r.error is None


class TestClassifyBroadForget:
    @pytest.mark.parametrize(
        "text",
        [
            "忘掉全部",
            "忘掉所有",
            "忘记全部记忆",
            "forget everything",
            "forget all",
            "Forget Everything!",
        ],
    )
    def test_scope_too_broad(self, text: str) -> None:
        r = classify_memory_command(text)
        assert r.intent == "forget"
        assert r.error == "scope_too_broad"
        assert r.payload_text == ""


class TestClassifyNone:
    @pytest.mark.parametrize(
        "text",
        [
            "今天天气不错",
            "你还记得我吗",
            "记得把伞带上",
            "别忘了明天开会",
            "别忘记明天开会",
            "don't forget the meeting tomorrow",
            "",
            "   ",
            "记住了吗",
            "我想起来了",
        ],
    )
    def test_not_explicit_command(self, text: str) -> None:
        r = classify_memory_command(text)
        assert r.intent == "none"
        assert r.error is None
        assert r.deixis is None
        assert r.payload_text == ""


class TestClassifyEdge:
    def test_non_string_none(self) -> None:
        r = classify_memory_command(None)  # type: ignore[arg-type]
        assert r.intent == "none"

    def test_whitespace_trim(self) -> None:
        r = classify_memory_command("  记住：  测试  ")
        assert r.intent == "remember"
        assert r.payload_text == "测试"

    def test_result_is_dataclass_like(self) -> None:
        r = classify_memory_command("记住这个")
        assert isinstance(r, MemoryCommandIntent)
