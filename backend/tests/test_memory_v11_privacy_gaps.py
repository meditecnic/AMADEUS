"""[129] Rework 1: preserve prose after synthetic credential values."""

import pytest

from app.services.memory_v11.jobs import redact_user_text
from app.services.memory_v11.privacy import classify_memory_sensitive_text


# Synthetic samples from the dispatch and its supplement; no account data.
_REWORK1_CASES = [
    ("ja-pin-length", "暗証番号は4桁です", None, None),
    ("ja-password-length", "パスワードは8文字以上です", None, None),
    ("ja-password-count", "パスワードは1つだけ", None, None),
    ("ja-passcode-length", "パスコードは6桁です", None, None),
    ("ja-strength", "パスワードはstrongです", None, None),
    ("ja-required-period", "パスワードはrequired。", None, None),
    ("cn-digit-count", "密码是4位数字", None, None),
    ("cn-strength", "密码是strong的", None, None),
    ("cn-length", "密码是8位以上", None, None),
    ("cn-requirement", "密码要求是8位以上", None, None),
    ("bearer-bonds", "Bearer bonds are risky", None, None),
    ("bearer-news", "the bearer of bad news", None, None),
    ("cn-short-prose", "密码是ab，你猜", None, None),
    ("ja-password-prose", "パスワードはhunter22です", "password", "hunter22"),
    ("cn-wifi-prose", "我的wifi密码是hunter22，帮我记住", "password", "hunter22"),
    ("url-cn-prose", "https://example.com/cb?token=abcdef123456，请帮我看看", "api_secret", "abcdef123456"),
    ("url-parenthesis", "(https://example.com/?token=abcdef123456)", "api_secret", "abcdef123456"),
    ("cn-short-password-continuation", "密码：ab，cd1234", "password", "ab，cd1234"),
    ("cn-short-api-continuation", "API密钥是ab，cdefgh123", "api_secret", "ab，cdefgh123"),
    ("cn-short-continuation-prose", "密码：ab，cd1234我记得", "password", "ab，cd1234"),
    ("ja-fullwidth-prose", "パスワードはｈｕｎｔｅｒ２２です", "password", "ｈｕｎｔｅｒ２２"),
    ("cn-fullwidth-continuation", "密码：ａｂ，ｃｄ１２３４我记得", "password", "ａｂ，ｃｄ１２３４"),
]


@pytest.mark.parametrize("case_id,text,category,value", _REWORK1_CASES, ids=[row[0] for row in _REWORK1_CASES])
def test_rework1_classification(case_id, text, category, value):
    assert classify_memory_sensitive_text(text) == (frozenset({category}) if category else frozenset())


@pytest.mark.parametrize("case_id,text,category,value", _REWORK1_CASES, ids=[row[0] for row in _REWORK1_CASES])
def test_rework1_partial_redaction(case_id, text, category, value):
    expected = text.replace(value, f"[REDACTED:{category}]", 1) if category else text
    masked, blocked = redact_user_text(text)
    assert blocked is False
    assert masked == expected
    if category:
        assert value not in masked
        assert classify_memory_sensitive_text(masked) == frozenset()


@pytest.mark.parametrize("cue,category,value", [
    ("我的密码是", "password", "hunter22"),
    ("密码是", "password", "hunter22"),
    ("密码：", "password", "hunter22"),
    ("密钥：", "api_secret", "abcdef123456"),
    ("访问令牌是", "api_secret", "abcdef123456"),
    ("パスワードは", "password", "hunter22"),
    ("暗証番号が", "password", "1234"),
    ("パスコード：", "password", "hunter22"),
    ("APIキーは", "api_secret", "abcdef123456"),
    ("アクセストークン＝", "api_secret", "abcdef123456"),
])
def test_rework1_cjk_cues_keep_unspaced_prose(cue, category, value):
    # A ligature and a combining character before the cue must not shift offsets.
    prefix = "ﬀ a\u030a ㍿ " + cue
    text = prefix + value + "覚えてね"
    assert classify_memory_sensitive_text(text) == frozenset({category})
    assert redact_user_text(text) == (prefix + f"[REDACTED:{category}]覚えてね", False)


@pytest.mark.parametrize("punctuation", list("，。；！？、）】」"))
@pytest.mark.parametrize("prefix,suffix,should_redact", [
    ("hunter22", "", True),
    ("ab", "cd1234", True),
    ("ab", "", False),
])
def test_rework1_cjk_punctuation_validates_prefix_and_continuation(punctuation, prefix, suffix, should_redact):
    text = "密码：" + prefix + punctuation + suffix + "我记得"
    category = frozenset({"password"}) if should_redact else frozenset()
    assert classify_memory_sensitive_text(text) == category
    if not should_redact:
        expected = text
    elif suffix:
        expected = "密码：[REDACTED:password]我记得"
    else:
        expected = "密码：[REDACTED:password]" + punctuation + "我记得"
    assert redact_user_text(text) == (expected, False)


@pytest.mark.parametrize("key,category,value", [
    ("token", "api_secret", "abcdef123456"),
    ("password", "password", "hunter22"),
])
@pytest.mark.parametrize("tail", ["汉字", "かな", "カナ", "ｶﾅ", *list("，。；！？、）】」)]'\"")])
def test_rework1_url_preserves_trailing_prose_and_delimiters(key, category, value, tail):
    prefix = "https://example.com/cb?" + key + "="
    text = prefix + value + tail
    assert classify_memory_sensitive_text(text) == frozenset({category})
    assert redact_user_text(text) == (prefix + f"[REDACTED:{category}]" + tail, False)
