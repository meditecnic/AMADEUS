from app.security.prompt import SYSTEM_PROMPT_BASE, COMPRESSION_PROMPT_SUFFIX

def test_prompt_contains_daru_mapping():
    assert "桶子" in SYSTEM_PROMPT_BASE
    assert "ダル" in SYSTEM_PROMPT_BASE

def test_prompt_contains_worldline_terms():
    assert "世界线" in SYSTEM_PROMPT_BASE
    assert "萨列里" in SYSTEM_PROMPT_BASE

def test_compression_prompt_contains_translation_mapping():
    assert "桶子" in COMPRESSION_PROMPT_SUFFIX

def test_fewshot_has_chinese_input_examples():
    import json
    data = json.load(open("prompts/kurisu_fewshot.json", encoding="utf-8"))
    user_contents = [m["content"] for m in data if m["role"] == "user"]
    assert any("克里斯蒂娜" in c for c in user_contents)
    assert any("桶子" in c for c in user_contents)
