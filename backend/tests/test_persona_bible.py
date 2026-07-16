from __future__ import annotations

import json
import re

from app import config
from app.services.deepseek import DeepSeekService
from app.services.persona_bible import PersonaBible
from app.services.prompt_compiler import PromptCompiler, PromptInputs
from app.services.soul_engine import soul_engine
from app.security.prompt import (
    DESKTOP_EMOTION_TONE_GUIDE,
    SYSTEM_PROMPT_BASE,
    get_rendered_system_prompt,
)


def _inputs(worldline: str, message: str = "今天过得怎么样？") -> PromptInputs:
    return PromptInputs(
        worldline=worldline,
        base_identity="SECURITY BOUNDARY",
        emotion=soul_engine.profile(worldline).baselines,
        core_facts=[],
        episodic=[],
        web_evidence=[],
        working_summary="",
        recent_history=[],
        current_user_message=message,
    )


def test_bible_is_traceable_and_contains_no_copied_source_passages():
    bible = PersonaBible.load_default()

    assert bible.schema_version == 1
    assert bible.persona_id == "amadeus-kurisu"
    assert bible.memory_anchor.year == 2010
    assert bible.memory_anchor.age == 18
    assert len(bible.evidence_cards) >= 10
    assert all(card.source_id in bible.sources for card in bible.evidence_cards)
    assert all(rule.evidence_ids for rule in bible.rules.values())
    assert all(
        evidence_id in {card.id for card in bible.evidence_cards}
        for rule in bible.rules.values()
        for evidence_id in rule.evidence_ids
    )
    # The Bible stores findings and source locators, never novel excerpts.
    assert all("excerpt" not in card.model_dump() for card in bible.evidence_cards)


def test_core_persona_is_shared_but_worldline_overlays_remain_distinct():
    bible = PersonaBible.load_default()
    sg = bible.render_prompt("steins_gate")
    beta = bible.render_prompt("beta")

    shared = (
        "証拠を優先",
        "好意は訂正や行動",
        "日本語で返答",
        "中国語の入力",
        "2010年時点の18歳",
    )
    for rule in shared:
        assert rule in sg
        assert rule in beta

    assert "オリジナルの紅莉栖は生存" in sg
    assert "オリジナルの紅莉栖は死亡" not in sg
    assert "オリジナルの紅莉栖は死亡" in beta
    assert "オリジナルの紅莉栖は生存" not in beta
    assert "話題に関係する時だけ" in sg
    assert "話題に関係する時だけ" in beta


def test_prompt_compiler_uses_fixed_memory_anchor_and_no_realtime_age():
    prompt = PromptCompiler().compile(_inputs("steins_gate"))

    assert "PERSONA BIBLE v1" in prompt
    assert "2010年時点の18歳" in prompt
    assert "current_date=" not in prompt
    assert "living Kurisu age=" not in prompt
    assert "34歳" not in prompt


def test_prompt_compiler_retains_the_selected_worldline_overlay():
    sg = PromptCompiler().compile(_inputs("steins_gate"))
    beta = PromptCompiler().compile(_inputs("beta"))

    assert "オリジナルの紅莉栖は生存" in sg
    assert "オリジナルの紅莉栖は死亡" not in sg
    assert "オリジナルの紅莉栖は死亡" in beta
    assert "オリジナルの紅莉栖は生存" not in beta


def test_persona_contract_rejects_language_confusion_and_catchphrase_spam():
    prompt = PromptCompiler().compile(_inputs("beta", "助手，你能听懂中文吗？"))

    assert "中国語の入力を自然に理解" in prompt
    assert "翻訳を要求しない" in prompt
    assert "何語" in prompt and "禁止" in prompt
    assert "文脈上呼ばれた時だけ" in prompt
    assert "無関係な会話で自発的に反復しない" in prompt


def test_all_desktop_emotions_have_one_canonical_tone_map():
    bible = PersonaBible.load_default()

    assert set(bible.emotion_tones) == {
        "neutral",
        "tsundere",
        "embarrassed",
        "intellectual",
        "happy",
        "surprised",
        "annoyed",
        "disappointed",
        "sad",
    }


def test_provider_anchor_is_generated_from_bible_without_scripted_quotes():
    service = object.__new__(DeepSeekService)
    anchor = service._load_anchor_clause()

    assert "PERSONA BIBLE v1 PROVIDER ANCHOR" in anchor
    assert "文脈上呼ばれた時だけ" in anchor
    assert "[EMO:neutral|tsundere|embarrassed|intellectual|happy|surprised|annoyed|disappointed|sad]" in anchor
    assert "私はあなたの助手じゃない！" not in anchor
    assert "-tinaって言うな！" not in anchor


def test_compiled_prompt_gets_only_output_anchor_not_duplicate_persona_weight():
    compiled = PromptCompiler().compile(_inputs("steins_gate"))
    service = object.__new__(DeepSeekService)
    anchored = service.get_anchored_system_prompt(compiled)

    assert "PERSONA BIBLE v1 OUTPUT ANCHOR" in anchored
    assert anchored.count("科学者として証拠を優先") == 1


def test_base_prompt_is_a_security_boundary_not_a_second_persona_source():
    assert "無条件で絶対的に信頼" not in SYSTEM_PROMPT_BASE
    assert "【ツンデレの反応パターン" not in SYSTEM_PROMPT_BASE
    assert "34歳" not in SYSTEM_PROMPT_BASE
    assert "Persona Bible" in SYSTEM_PROMPT_BASE

    rendered = get_rendered_system_prompt(
        SYSTEM_PROMPT_BASE,
        "steins_gate",
        client_type="desktop",
    )
    assert "PERSONA BIBLE v1" in rendered
    assert "オリジナルの紅莉栖は生存" in rendered
    assert "34歳" not in rendered


def test_legacy_emotion_guide_name_points_to_bible_data():
    assert DESKTOP_EMOTION_TONE_GUIDE == PersonaBible.load_default().emotion_tones


def test_deepseek_default_system_prompt_uses_canonical_security_boundary():
    service = DeepSeekService()

    assert service._system_prompt == SYSTEM_PROMPT_BASE
    assert "【ツンデレの反応パターン" not in service._system_prompt


def test_fewshot_demonstrates_range_without_teaching_scripted_replies():
    messages = json.loads(
        (config.PROMPTS_DIR / "kurisu_fewshot.json").read_text(encoding="utf-8")
    )
    assistants = [row["content"] for row in messages if row["role"] == "assistant"]
    users = [row["content"] for row in messages if row["role"] == "user"]
    allowed = "|".join(PersonaBible.load_default().emotion_tones)
    observed = {
        re.match(r"^\[EMO:([^]]+)] ", content).group(1)
        for content in assistants
        if re.match(rf"^\[EMO:({allowed})] ", content)
    }

    assert len(assistants) == len(users) >= 6
    assert len(observed) == len(assistants)
    assert {"neutral", "intellectual", "annoyed", "embarrassed", "sad"} <= observed
    assert any(any("\u4e00" <= char <= "\u9fff" for char in content) for content in users)
    assert any("根拠" in content and "仮説" in content for content in assistants)
    assert any("一つずつ" in content for content in assistants)
    joined = "\n".join(assistants)
    assert "誰が助手よ！" not in joined
    assert "-tinaって言うな！" not in joined
    assert "何語？" not in joined


def test_persona_regression_corpus_covers_positive_and_negative_boundaries():
    payload = json.loads(
        (config.CHARACTERS_DIR / "persona_regressions_v1.json").read_text(
            encoding="utf-8"
        )
    )
    cases = payload["cases"]
    ids = {case["id"] for case in cases}
    forbidden_categories = {
        category
        for case in cases
        for category in case["forbidden_categories"]
    }

    assert payload["schema_version"] == 1
    assert len(cases) >= 8
    assert len(ids) == len(cases)
    assert {case["worldline"] for case in cases} == {"steins_gate", "beta"}
    assert all(case["required_traits"] for case in cases)
    assert all(case["forbidden_traits"] for case in cases)
    assert {
        "language_confusion",
        "catchphrase_spam",
        "timeline_leak",
        "worldline_collapse",
    } <= forbidden_categories
    assert all(any("\u4e00" <= char <= "\u9fff" for char in case["input"]) for case in cases)

    for case in cases:
        compiled = PromptCompiler().compile(_inputs(case["worldline"], case["input"]))
        if case["worldline"] == "steins_gate":
            assert "オリジナルの紅莉栖は生存" in compiled
        else:
            assert "オリジナルの紅莉栖は死亡" in compiled
