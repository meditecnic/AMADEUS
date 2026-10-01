from app.security.prompt import SYSTEM_PROMPT_BASE

def test_prompt_contains_daru_mapping():
    assert "桶子" in SYSTEM_PROMPT_BASE
    assert "ダル" in SYSTEM_PROMPT_BASE

def test_prompt_contains_worldline_terms():
    assert "世界线" in SYSTEM_PROMPT_BASE
    assert "萨列里" in SYSTEM_PROMPT_BASE

def test_fewshot_handles_chinese_without_language_confusion():
    import json
    data = json.load(open("prompts/kurisu_fewshot.json", encoding="utf-8"))
    user_contents = [m["content"] for m in data if m["role"] == "user"]
    assistant_contents = [m["content"] for m in data if m["role"] == "assistant"]
    assert len(user_contents) >= 6
    assert all(any("\u4e00" <= char <= "\u9fff" for char in content) for content in user_contents)
    assert all(content.startswith("[EMO:") for content in assistant_contents)
    assert not any("何語" in content or "翻訳して" in content for content in assistant_contents)
