"""[134] Partial masking, generated-marker boundaries and credential suffixes."""

import pytest

from app.services.memory_v11.jobs import redact_user_text
from app.services.memory_v11.privacy import (
    classify_memory_sensitive_text,
    find_memory_sensitive_matches,
)


# All values are synthetic. Keep these cases available for the report's
# baseline/head comparison without importing the backend or calling a model.
_CASES = [
    ("cn-parenthesis", "密码是hunter22（wifi）", "password", "hunter22"),
    ("cn-assignment", "密码：hunter22（test）", "password", "hunter22"),
    ("api-parenthesis", "API密钥：abcdefghijkl（test）", "api_secret", "abcdefghijkl"),
    ("review-api-parenthesis", "API密钥：abcdef123456（test）", "api_secret", "abcdef123456"),
    ("ja-parenthesis", "パスワードはhunter22（wifi）です", "password", "hunter22"),
    ("cn-colon", "密码是hunter22：wifi", "password", "hunter22"),
    ("period-year", "password: hunter2. 2024 was a great year", "password", "hunter2"),
    ("period-prose", "password: hunter2. Running is my hobby", "password", "hunter2"),
    ("semicolon-prose", "api_key=abcdefgh; Running2024 is my hobby", "api_secret", "abcdefgh"),
    ("typed-marker-year", "password: [REDACTED:password]. 2024 was a great year", "password", "[REDACTED:password]. 2024"),
    ("english-han", "my password is hunter22帮我记住这个", "password", "hunter22"),
    ("english-prose", "password is required，我记得", None, None),
    ("english-kana", "password is hunter22覚えてね", "password", "hunter22"),
    ("api-kana", "api key is abcdefghijklカナ", "api_secret", "abcdefghijkl"),
    ("ascii-quote", '?token="abcdef123456"', "api_secret", "abcdef123456"),
    ("single-quote", "?token='abcdef123456'", "api_secret", "abcdef123456"),
    ("curly-quote", "?token=“abcdef123456”", "api_secret", "abcdef123456"),
    ("ja-quote", "?token=「abcdef123456」", "api_secret", "abcdef123456"),
    ("added-parenthesis", "密码是hunter22（备用）", "password", "hunter22"),
    ("added-colon", "密码是hunter22：备用", "password", "hunter22"),
    ("added-quotes", "密码是hunter22“备用”", "password", "hunter22"),
    ("added-brackets", "密码是hunter22【备用】", "password", "hunter22"),
    ("strong-suffix", "password: Pass，word99", "password", "Pass，word99"),
    ("wide-suffix", "我的密码是Hello!2024，ｗｏｒｄ９９", "password", "Hello!2024，ｗｏｒｄ９９"),
    ("copula-suffix", "password is Pass，word99", "password", "Pass，word99"),
    ("cn-suffix", "我的密码是Pass，word99", "password", "Pass，word99"),
    ("wide-semicolon", "密码：Hello!2024；ｘｙｚ１２３", "password", "Hello!2024；ｘｙｚ１２３"),
    ("wide-api-suffix", "API密钥是abcdefgh，ｉｊｋｌ５６７８", "api_secret", "abcdefgh，ｉｊｋｌ５６７８"),
    ("wide-symbol-suffix", "password: Pass，＄＃ｗｏｒｄ９９", "password", "Pass，＄＃ｗｏｒｄ９９"),
    ("cn-stop", "我的密码是Hello!2024，我喜欢猫", "password", "Hello!2024"),
    ("english-stop", "password: hunter22，I like cats", "password", "hunter22"),
    ("symbol-suffix", "password: Pass，!!!abc", "password", "Pass，!!!abc"),
    ("suffix-boundary", "password: Pass，word99（wifi）", "password", "Pass，word99"),
    ("symbols-only-suffix", "password: Pass，$#", "password", "Pass，$#"),
    ("repeated-suffix", "password: Pass，word99：tail7（wifi）", "password", "Pass，word99：tail7"),
]


@pytest.mark.parametrize("case_id,text,category,value", _CASES, ids=[row[0] for row in _CASES])
def test_v134_classification(case_id, text, category, value):
    assert classify_memory_sensitive_text(text) == (frozenset({category}) if category else frozenset())
    assert {text[start:end] for start, end, _ in find_memory_sensitive_matches(text)} == (
        {value} if category else set()
    )


@pytest.mark.parametrize("case_id,text,category,value", _CASES, ids=[row[0] for row in _CASES])
def test_v134_partial_redaction(case_id, text, category, value):
    expected = text.replace(value, f"[REDACTED:{category}]", 1) if category else text
    assert redact_user_text(text) == (expected, False)


# [140] Every dispatch sentence, followed by the stated grammar boundaries.
# Values are synthetic; expected spans refer to the original, unnormalized text.
_V140_CASES = [
    ("1", "我的password: hunter22", "password", "hunter22"),
    ("2", "我的password是hunter22", "password", "hunter22"),
    ("3", "wifi的password是hunter22", "password", "hunter22"),
    ("4", "password是hunter22", "password", "hunter22"),
    ("5", "ｐａｓｓｗｏｒｄ：ｑｗｅｒｔｙ９９", "password", "ｑｗｅｒｔｙ９９"),
    ("6", "ＰＡＳＳＷＯＲＤ: hunter22", "password", "hunter22"),
    ("7-wide", "和ｏｔｐ 123456", "otp", "123456"),
    ("7", "和otp 123456", "otp", "123456"),
    ("8-compact", "APIkey是sk-abcdefghijklmnop", "api_secret", "sk-abcdefghijklmnop"),
    ("8", "api key是abcdefgh12345678", "api_secret", "abcdefgh12345678"),
    ("9", "PIN是1234", "password", "1234"),
    ("9-code", "PIN码是1234", "password", "1234"),
    ("9-owned", "我的pin是1234", "password", "1234"),
    ("9-colon", "PIN: 1234", "password", "1234"),
    ("9-wide-colon", "PIN码：123456", "password", "123456"),
    ("10", "token: abcdef123456", "api_secret", "abcdef123456"),
    ("10-copula", "token是abcdef123456", "api_secret", "abcdef123456"),
    ("10-github", "github token: ghp_abcdefgh12345678", "api_secret", "ghp_abcdefgh12345678"),
    ("11-password", "password: Johnson’sbirthday", "password", "Johnson’sbirthday"),
    ("11-api", "api_key=abcdefgh1234’xyz", "api_secret", "abcdefgh1234’xyz"),
]

_V140_PROSE = [
    "password是什么", "我的password忘了", "password是required的", "密码学很难",
    "pinyin是什么", "ping是1234ms", "spinning class", "我的PIN忘了", "PIN码是什么",
    "token是什么意思", "max token: 4096", "token数量是1000000", "token: 128k",
    "token是gpt4o", "我的secret是我喜欢你", "APIkey是什么", "Johnson’s birthday",
]

_V140_GRAMMAR = [
    ("password-cn-is", "password为hunter22", "password", "hunter22"),
    ("password-cn-equals", "password等于hunter22", "password", "hunter22"),
    ("api-cn-is", "api_key为abcdefgh12345678", "api_secret", "abcdefgh12345678"),
    ("api-cn-equals", "api key等于abcdefgh12345678", "api_secret", "abcdefgh12345678"),
    ("ascii-left", "xpassword: hunter22", None, None),
    ("ascii-digit-left", "1password: hunter22", None, None),
    ("ascii-right", "password1: hunter22", None, None),
    ("api-ascii-left", "xAPIkey是abcdefgh12345678", None, None),
    ("otp-ascii-left", "xotp 123456", None, None),
    ("otp-ascii-right", "otp1 123456", None, None),
    ("kana-cue", "のpassword: hunter22", "password", "hunter22"),
    ("bare-sk-han", "和sk-abcdefghijklmnop", "api_secret", "sk-abcdefghijklmnop"),
    ("bare-pk-kana", "のpk-abcdefghijklmnop", "api_secret", "pk-abcdefghijklmnop"),
    ("bare-ascii-left", "xsk-abcdefghijklmnop", None, None),
    ("wide-offsets", "ﬀ a\u030a ㍿ ＰＡＳＳＷＯＲＤ：ｈｕｎｔｅｒ２２覚えてね", "password", "ｈｕｎｔｅｒ２２"),
    ("wide-api", "我的ＡＰＩｋｅｙ是ａｂｃｄｅｆｇｈ１２３４５６７８", "api_secret", "ａｂｃｄｅｆｇｈ１２３４５６７８"),
    ("wide-otp", "和ｏｔｐ １２３４５６", "otp", "１２３４５６"),
    ("pin-traditional", "pin碼＝１２３４", "password", "１２３４"),
    ("pin-code", "pin code is 1234", "password", "1234"),
    ("pin-number", "PIN number等于123456789012", "password", "123456789012"),
    ("pin-cn-is", "pin为1234", "password", "1234"),
    ("pin-equal", "pin=1234", "password", "1234"),
    ("pin-wide", "ＰＩＮ：１２３４", "password", "１２３４"),
    ("pin-short", "PIN: 123", None, None),
    ("pin-long", "PIN: 1234567890123", None, None),
    ("pin-alphanumeric", "PIN: 1234abcd", None, None),
    ("pin-no-assignment", "pin 1234", None, None),
    ("token-is", "token is abcdef123456", "api_secret", "abcdef123456"),
    ("token-cn-is", "token为abcdef123456", "api_secret", "abcdef123456"),
    ("token-cn-equals", "token等于abcdef123456", "api_secret", "abcdef123456"),
    ("token-equal", "token=abcdef123456", "api_secret", "abcdef123456"),
    ("token-wide", "ｔｏｋｅｎ：ａｂｃｄｅｆ１２３４５６", "api_secret", "ａｂｃｄｅｆ１２３４５６"),
    ("token-short", "token: abcdef12345", None, None),
    ("token-letters", "token: abcdefghijkl", None, None),
    ("token-digits", "token: 123456789012", None, None),
    ("token-ascii-left", "xtoken: abcdef123456", None, None),
    ("token-ascii-right", "token1: abcdef123456", None, None),
    ("curly-left", "password: Johnson‘sbirthday", "password", "Johnson‘sbirthday"),
    ("curly-leading", "password: ‘hunter22", "password", "‘hunter22"),
    ("curly-trailing", "password: hunter22’", "password", "hunter22"),
    ("curly-prose", "password: hunter22’かな", "password", "hunter22"),
]


@pytest.mark.parametrize("case_id,text,category,value", _V140_CASES + _V140_GRAMMAR,
                         ids=[row[0] for row in _V140_CASES + _V140_GRAMMAR])
def test_v140_redaction(case_id, text, category, value):
    assert classify_memory_sensitive_text(text) == (frozenset({category}) if category else frozenset())
    expected = text.replace(value, f"[REDACTED:{category}]", 1) if category else text
    assert redact_user_text(text) == (expected, False)
    assert {text[start:end] for start, end, _ in find_memory_sensitive_matches(text)} == (
        {value} if category else set()
    )


@pytest.mark.parametrize("text", _V140_PROSE)
def test_v140_prose_is_preserved(text):
    assert classify_memory_sensitive_text(text) == frozenset()
    assert find_memory_sensitive_matches(text) == []
    assert redact_user_text(text) == (text, False)
