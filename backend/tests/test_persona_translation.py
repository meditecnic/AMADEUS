import hashlib
import json
import re

from app import config
from app.services.persona_bible import persona_bible
from app.security.prompt import SYSTEM_PROMPT_BASE

def test_prompt_contains_daru_mapping():
    assert "桶子" in SYSTEM_PROMPT_BASE
    assert "ダル" in SYSTEM_PROMPT_BASE

def test_prompt_contains_worldline_terms():
    assert "世界线" in SYSTEM_PROMPT_BASE
    assert "萨列里" in SYSTEM_PROMPT_BASE

def test_fewshot_preserves_the_signed_japanese_dialogues():
    data = json.loads((config.PROMPTS_DIR / "kurisu_fewshot.json").read_text(encoding="utf-8"))
    assert [scene["id"] for scene in data] == [
        f"{pair}{number}" for pair in "ABCDEF" for number in (1, 2)
    ]
    texts = []
    for scene in data:
        turns = scene["turns"]
        assert len(turns) in (4, 6, 8)
        assert [turn["role"] for turn in turns] == ["user", "assistant"] * (len(turns) // 2)
        for turn in turns:
            content = turn["content"]
            assert content.strip() and not any(word in content for word in ("何語", "翻訳して"))
            if turn["role"] == "assistant":
                match = re.match(r"^\[EMO:([a-z]+)\]\s+\S", content)
                assert match and match[1] in persona_bible.emotion_tones
            texts.append(content)

    # [294] signed Japanese dialogue (A1 fake timestamp removed for round 2); Markdown fences removed.
    assert len(texts) == 76
    assert hashlib.sha256("\0".join(texts).encode("utf-8")).hexdigest() == (
        "3863a1bd361ea9ac362d760cf0cd13585714e889350b52fe4820708bb233170a"
    )
