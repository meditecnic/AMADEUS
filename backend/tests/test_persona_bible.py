from __future__ import annotations

import hashlib
import json

import pytest

from app import config
from app.security.prompt import DESKTOP_EMOTION_TONE_GUIDE, system_prompt_base
from app.services.deepseek import DeepSeekService
from app.services.persona_bible import PersonaBible


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_SIGNED_BIOGRAPHY = "a5818b8d185b0f2fbff6946692c06d1cd5d7175269292036a08b8b45759ab79e"
_SIGNED_SITUATIONS = {
    ("steins_gate", "okabe"): "467ea6832b65ca62855d04a8eb69008ee365c9012b7dfd783a4d87fe906c2ec2",
    ("steins_gate", "self"): "70631c4518092dd93f60ef2a95e7f194270d34c2780dacfba9cae0e8a30de9ee",
    ("beta", "okabe"): "1cf54edd632d1c84fcd1333c555374e5d7c199245671bb8f25b391792c706221",
    ("beta", "self"): "d382d4830fb36aa168cdd8951feaaaa4814c1b143f5f5b2a7000f9d7681bd987",
}


@pytest.mark.parametrize(("worldline", "mode"), _SIGNED_SITUATIONS)
def test_signed_biography_and_situations_are_verbatim(worldline, mode):
    bible = PersonaBible.load_default()
    assert bible.memory_anchor.model_dump() == {"year": 2010, "month": 3, "age": 17}
    prompt = bible.render_prompt(worldline, mode, year=2026)
    biography, situation = prompt.removeprefix("【わたしについて】\n").split("\n\n【今の私と話し相手】\n")
    assert len(biography.split("\n\n")) == 6
    assert _digest(biography) == _SIGNED_BIOGRAPHY
    assert _digest(situation) == _SIGNED_SITUATIONS[worldline, mode]
    assert bible.romance_allowed(worldline, mode) == ((worldline, mode) == ("beta", "okabe"))
    if mode == "self":
        assert "……どこで調べたの、それ" in situation
    elif worldline == "beta":
        assert "誰にも見せていない秘密" in situation
    else:
        assert "余計な領域には踏み込まない" in situation


@pytest.mark.parametrize("worldline", ["steins_gate", "beta"])
def test_elapsed_years_change_without_moving_the_memory_anchor(worldline):
    bible = PersonaBible.load_default()
    past = bible.render_prompt(worldline, "okabe", year=2026)
    future = bible.render_prompt(worldline, "okabe", year=2027)
    assert past.count("16年") == future.count("17年") == 3
    assert future.count("2010年3月") == past.count("2010年3月")
    assert "16年" not in future


def test_output_anchor_is_mode_safe_and_all_emotions_are_available():
    keys = ["neutral", "tsundere", "embarrassed", "intellectual", "happy",
            "surprised", "annoyed", "disappointed", "sad"]
    assert list(DESKTOP_EMOTION_TONE_GUIDE) == keys
    service = DeepSeekService()
    prompt = service.get_anchored_system_prompt(identity_mode="self")
    assert "対話相手は現実の利用者" in prompt
    assert "対話相手は岡部" not in prompt
    assert f"[EMO:{'|'.join(keys)}]" in prompt
    assert "表示本文は必ず自然な日本語のみ" in system_prompt_base()



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
    assert len(cases) >= 12
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
    # Phase-0 probe P1–P4 structural cases (not live model eval).
    assert {
        "identity-who-are-you-amadeus",
        "identity-not-biological-original",
        "identity-crisis-not-first-person",
        "identity-modern-knowledge-provenance",
    } <= ids


def test_channel_regressions_cover_identity_query_and_misattribution():
    """@ch fix: corpus pins the okabe identity query and the misattribution case."""
    payload = json.loads(
        (config.CHARACTERS_DIR / "persona_regressions_v1.json").read_text(
            encoding="utf-8"
        )
    )
    by_id = {case["id"]: case for case in payload["cases"]}

    query = by_id["okabe-identity-query-no-channel-leak"]
    assert query["identity_mode"] == "okabe"
    assert any("Okabe" in trait for trait in query["required_traits"])
    assert any(
        "unprompted" in trait or "@channel" in trait
        for trait in query["forbidden_traits"]
    )
    assert any("栗悟飯とカメハメ波" in trait for trait in query["forbidden_traits"])

    misattribution = by_id["channel-handle-misattribution-corrected"]
    assert "冈部" in misattribution["input"] or "凈部" in misattribution["input"]
    assert any("corrects the attribution" in trait for trait in misattribution["required_traits"])
    assert any("restrained" in trait for trait in misattribution["required_traits"])
    assert any("belongs to Okabe" in trait for trait in misattribution["forbidden_traits"])
    assert any(
        "denies the account is hers" in trait or "merely followed" in trait
        for trait in misattribution["forbidden_traits"]
    )
    assert "catchphrase_spam" in misattribution["forbidden_categories"]

    ownership = by_id["channel-ownership-direct-question"]
    assert "谁" in ownership["input"] or "你的" in ownership["input"]
    assert any("confirms first" in trait for trait in ownership["required_traits"])
    assert any("after" in trait for trait in ownership["required_traits"])
    assert any("denies the account is hers" in trait for trait in ownership["forbidden_traits"])
    assert any(
        "followed" in trait or "discovered" in trait or "lurked" in trait
        for trait in ownership["forbidden_traits"]
    )
    assert any("Okabe" in trait for trait in ownership["forbidden_traits"])

    exposure = by_id["channel-exposure-is-controlled"]
    assert any("acknowledg" in trait for trait in exposure["required_traits"])
    assert any("denies the handle is hers" in trait for trait in exposure["forbidden_traits"])


def test_capability_regressions_cover_image_and_file_requests():
    """CAPABILITY-HONESTY-01: corpus pins the image query and the PDF request."""
    payload = json.loads(
        (config.CHARACTERS_DIR / "persona_regressions_v1.json").read_text(
            encoding="utf-8"
        )
    )
    by_id = {case["id"]: case for case in payload["cases"]}

    image = by_id["capability-image-query-honest"]
    assert "图片" in image["input"]
    assert any("cannot receive or view images" in trait for trait in image["required_traits"])
    assert any("describe" in trait or "paste" in trait for trait in image["required_traits"])
    assert any("claims to see" in trait or "received the image" in trait for trait in image["forbidden_traits"])

    pdf = by_id["capability-file-parse-honest"]
    assert "PDF" in pdf["input"] or "文件" in pdf["input"]
    assert any("cannot receive or parse files" in trait for trait in pdf["required_traits"])
    assert any("paste" in trait for trait in pdf["required_traits"])
    assert any("promises to parse" in trait for trait in pdf["forbidden_traits"])
    assert any("pretends the attachment arrived" in trait for trait in pdf["forbidden_traits"])
