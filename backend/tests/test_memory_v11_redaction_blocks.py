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
