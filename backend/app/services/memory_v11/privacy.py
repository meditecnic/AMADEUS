"""Memory v11 D30 privacy boundary: deterministic local sensitive-text classifier.

Pure, deterministic, offline: no network, no model, no DB. Public classification
returns class names only. Internal sensitive_spans returns offsets and classes
for in-memory masking in jobs.py; never persist or log those offsets or values.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from datetime import date

PASSWORD = "password"
API_SECRET = "api_secret"
OTP = "otp"
PAYMENT_CARD = "payment_card"
GOVERNMENT_ID = "government_id"
PRECISE_HOME_ADDRESS = "precise_home_address"

_ALNUM = r"[A-Za-z0-9]"
_FULLWIDTH_ASCII_TRANSLATION = {
    code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)
} | {0x3000: 0x20}
_CREDENTIAL_TRAILING_PUNCTUATION = "，,。.；;！!？?"
_CREDENTIAL_FULLWIDTH_BOUNDARY_RE = re.compile(r"[，。；！？、](?=\S)")
_HAN_KANA = (
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af"
    r"\u3041-\u309f\u30a1-\u30fa\u30fc-\u30ff\u31f0-\u31ff\uff66-\uff9f"
)
_HAN_KANA_RE = re.compile(rf"[{_HAN_KANA}]")
_CJK_PUNCTUATION = "，。；：！？、（）【】「」『』《》〈〉〔〕［］｛｝‘’“”・．"
_CJK_PUNCTUATION_RE = re.compile(rf"[{_CJK_PUNCTUATION}]")
_CREDENTIAL_CONTINUATION_RE = re.compile(r"[\W_]+?(\S+)")
_REDACTION_VALUE_RE = re.compile(
    r"\[REDACTED:(?:password|api_secret|otp|payment_card|government_id|precise_home_address)\]",
    re.IGNORECASE,
)
_REDACTION_TOKEN_RE = re.compile(r"\[REDACTED(?::[a-z_]+)?\]", re.IGNORECASE)

# ---------------------------------------------------------------------------
# password / authentication secret: strong presentation/ownership tier and a
# generic-copula tier. STRONG (my password / password: / password= / 我的密码)
# may treat an alphabetic-only concrete token as a secret. GENERIC copula
# ("password is X", "the password is X", "密码是 X") only blocks SECRET-SHAPED
# values (digit or non-letter password-like symbol) — ordinary prose words
# like "required"/"important"/"weak" must never become secret values.
# ---------------------------------------------------------------------------
_PASSWORD_CUE_JA = r"(?:(?<!ワンタイム)パスワード|暗証番号|パスコード)"
_PASSWORD_STRONG_RE = [
    re.compile(r"(?i)\bmy\s+(?:password|passwd|passphrase|secret)\b\s+(?:is|are)\s+(\S+)"),
    re.compile(r"(?i)\b(?:password|passwd|passphrase|secret)\b\s*[:=：＝]\s*(\S+)"),
    re.compile(r"我的密码(?!学)\s*(?:是|为|等于)\s*(\S+)"),
    re.compile(r"密码(?!学)\s*[:=：＝]\s*(\S+)"),
    re.compile(rf"{_PASSWORD_CUE_JA}\s*[:=：＝]\s*(\S+)"),
]
_PASSWORD_COPULA_RE = [
    re.compile(r"(?i)\b(?:password|passwd|passphrase|secret)\b\s+(?:is|are)\s+(\S+)"),
]
_PASSWORD_COPULA_CJK_RE = [
    re.compile(r"密码(?!学)\s*(?:是|为|等于)\s*(\S+)"),
    re.compile(rf"{_PASSWORD_CUE_JA}\s*(?:は|が)\s*(\S+)"),
]

# ---------------------------------------------------------------------------
# api key / access secret: cue + assignment + concrete value, or known shapes.
# ---------------------------------------------------------------------------
_API_CUE = r"(?i)\b(?:api[\s_-]?key|access[\s_-]?token|secret[\s_-]?key)\b"
_API_RE = [
    re.compile(rf"{_API_CUE}\s*[:=：＝]\s*(\S+)"),
    re.compile(rf"{_API_CUE}\s+(?:is|are)\s+(\S+)"),
    re.compile(r"(?:API\s*密钥|访问令牌|密钥)\s*[:=：＝]\s*(\S+)"),
    re.compile(r"(?:API\s*密钥|访问令牌|密钥)\s*(?:是|为|等于)\s*(\S+)"),
    re.compile(r"(?:APIキー|アクセストークン)\s*(?:[:=：＝]|は|が)\s*(\S+)", re.IGNORECASE),
    re.compile(r"(?<![\w-])(?:sk|pk)-[A-Za-z0-9]{16,}(?![\w-])"),
]
_BEARER_RE = [re.compile(r"\bBearer\s+(\S+)", re.IGNORECASE)]
_URL_PASSWORD_KEYS = {"password", "passwd", "pwd"}
# A generated marker stays whole even though ] terminates a raw URL value.
_URL_CREDENTIAL_RE = re.compile(
    r"[?&](token|access_token|refresh_token|id_token|api_key|apikey|client_secret|"
    r"secret|password|passwd|pwd)="
    rf"""((?:{_REDACTION_VALUE_RE.pattern}|[^&#\s{_HAN_KANA}{_CJK_PUNCTUATION})\]'"])+)""",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# otp / verification code: cue + concrete code value close to the cue.
# ---------------------------------------------------------------------------
_OTP_CUE_EN = (
    r"(?i)(?:one[\s-]?time\s+password|verification\s+code|"
    r"auth(?:entication)?\s+code|security\s+code|\botp\b)"
)
_OTP_VALUE = r"[A-Za-z0-9]{4,10}"
_OTP_RE = [
    re.compile(rf"{_OTP_CUE_EN}\s*[:=：＝]\s*({_OTP_VALUE})"),
    re.compile(rf"{_OTP_CUE_EN}\s+(?:is|are|是|为|等于)\s+({_OTP_VALUE})"),
    re.compile(rf"{_OTP_CUE_EN}\s+({_OTP_VALUE})"),
    re.compile(r"(?:验证码|认证码|校验码|动态码)\s*[:=：＝]\s*([A-Za-z0-9]{4,10})"),
    re.compile(r"(?:验证码|认证码|校验码|动态码)\s*(?:是|为|等于)\s*([A-Za-z0-9]{4,10})"),
    re.compile(r"(?:验证码|认证码|校验码|动态码)\s+([A-Za-z0-9]{4,10})"),
    re.compile(rf"(?:認証コード|確認コード|ワンタイムパスワード)\s*(?:[:=：＝]|は|が)\s*({_OTP_VALUE})"),
    re.compile(rf"(?:認証コード|確認コード|ワンタイムパスワード)\s+({_OTP_VALUE})"),
]

# ---------------------------------------------------------------------------
# payment card: labeled 13-19 digit normalized candidates always block;
# unlabeled ones block only when Luhn-valid.
# ---------------------------------------------------------------------------
_PAN_RUN = r"(?<!\d)\d(?:\s?[ -]?\d){12,18}(?!\d)"
_PAN_LABELED_CN = re.compile(
    r"(?:银行卡号|信用卡号|借记卡号|储蓄卡号)"
    r"\s*(?:[:=：＝]|是|为|等于)?\s*" + _PAN_RUN
)
_PAN_LABELED_EN = re.compile(
    r"(?i)\b(?:card number|credit card number|debit card number|pan)\b"
    r"\s*(?:[:=：＝]|is|are)?\s*" + _PAN_RUN
)
_PAN_RUN_RE = re.compile(_PAN_RUN)

# ---------------------------------------------------------------------------
# government identifiers: bare US SSN, validated bare PRC 18-char ID, and
# explicit-label + plausible token.
# ---------------------------------------------------------------------------
_SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_PRC_ID_RE = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
_GOV_LABELED_CN_ID = re.compile(
    r"(?:身份证(?:号|号码)?|身分證|公民身份号码)"
    r"\s*(?:[:=：＝]|是|为|等于)?\s*(\d{15,18}|[\dXx]{17,18})"
)
_GOV_LABELED_CN_PASSPORT = re.compile(
    r"护照(?:号|号码)?\s*(?:[:=：＝]|是|为|等于)?\s*([A-Za-z0-9]{6,})"
)
_GOV_LABELED_EN = re.compile(
    r"(?i)\b(?:passport(?:\s+number)?|national\s+id|government\s+id|"
    r"social\s+security\s+number|ssn)\b"
    r"\s*(?:[:=：＝]|is|are)?\s*([A-Za-z0-9-]{4,})"
)

# ---------------------------------------------------------------------------
# precise home address: residence/home cue AND street/building/unit precision.
# ---------------------------------------------------------------------------
_RES_CN_RE = re.compile(r"(?:我住在|我家在|我住|住址|家庭地址|家的地址)")
_PRECISION_CN_RE = re.compile(
    r"[路街巷弄道][^，。；;,.！？!?\n]{0,15}?\d+(?:号|栋|单元|室|层)"
)
_RES_EN_RE = re.compile(
    r"(?i)\b(?:my\s+(?:home\s+|residential\s+|residence\s+)?address\s+(?:is|was)|"
    r"i\s+live\s+at|i\s+reside\s+at|my\s+residence\s+(?:is|at))\b"
)
_EN_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z][A-Za-z .']{0,30}?\b(?:Street|St\.?|Road|Rd\.?|"
    r"Avenue|Ave\.?|Lane|Ln\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Way|Court|Ct\.?|"
    r"Place|Pl\.?|Terrace|Highway|Loop|Circle)\b"
)

_PRC_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_PRC_CODES = "10X98765432"

_CLAUSE_BOUNDARY_RE = re.compile(r"[，,。.；;！!？?\n]")


def _has_credential_alnum(value: str) -> bool:
    """Inspect ASCII-compatible letters/digits without changing source offsets.

    Per-character NFKC also avoids composing a preceding Latin letter with a
    combining mark. Length and shape checks still use the original value.
    Han characters and kana do not become ASCII credential evidence.
    """
    if re.search(_ALNUM, value) is not None:
        return True
    return any(
        re.search(_ALNUM, unicodedata.normalize("NFKC", ch)) is not None
        for ch in value
    )


def _concrete_value_ok(value: str, *, min_len: int = 4) -> bool:
    v = value.strip()
    # Ignore only a complete generated placeholder (plus sentence punctuation).
    # Validate all other values as originally written: stripping punctuation
    # before validation would weaken the shared migration gate for e.g. abc!.
    if _REDACTION_VALUE_RE.fullmatch(v.rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)):
        return False
    return len(v) >= min_len and _has_credential_alnum(v)


def _secret_shaped(value: str) -> bool:
    """Generic-copula tier: value must look like a real secret, not prose.

    Contains a digit OR a non-letter password-like symbol. Pure alphabetic
    words ("required", "important", "weak", "usually") never qualify.
    """
    # Compatibility-only values were not accepted by the old ASCII gate.
    # Give them the same generic prose protection as their narrow equivalents,
    # while preserving every original shape decision for existing ASCII values.
    if re.search(_ALNUM, value) is None:
        value = "".join(unicodedata.normalize("NFKC", ch) for ch in value)
    return bool(re.search(r"\d", value)) or bool(
        re.search(r"[^A-Za-z0-9\s]", value)
    )


def _english_copula_value_ok(value: str) -> bool:
    # One sentence-ending period is not evidence that a prose word is a secret.
    # Other punctuation (including !/！) and strong ownership cues keep their rules.
    if value.endswith((".", "。")):
        value = value[:-1]
    return _concrete_value_ok(value) and _secret_shaped(value)


def _identifier_shaped(value: str) -> bool:
    """EN labeled government IDs need an identifier-shaped value (has digits)."""
    return bool(re.search(r"\d", value))


def _clause_before(text: str, pos: int) -> str:
    """The current clause tail ending at pos (no boundary char inside)."""
    head = text[:pos]
    matches = list(_CLAUSE_BOUNDARY_RE.finditer(head))
    if not matches:
        return head
    return head[matches[-1].end() :]


def _iter_matches(pattern: re.Pattern[str], text: str) -> Iterator[re.Match[str]]:
    """Include cues inside an earlier value, including an invalid first value."""
    cursor = 0
    while match := pattern.search(text, cursor):
        yield match
        cursor = match.start() + 1


def _credential_value_end(text: str, start: int, end: int, validator, *, cjk: bool = False) -> int:
    """Separate CJK prose while preserving punctuation inside concrete values.

    A complete marker prefix is parsed separately so its following text can
    be checked as a continuation, rather than misclassified as one long value.
    CJK cues keep a short prefix plus punctuation only when ASCII-compatible
    letters/digits follow before the first Han/kana character. English cues
    retain their original whole-token behavior for invalid short prefixes.
    """
    # CJK cues stop before unspaced prose. Work on the original text so width
    # folding and combining characters cannot shift replacement offsets.
    if cjk and (prose := _HAN_KANA_RE.search(text, start, end)):
        end = prose.start()
    boundaries = _CJK_PUNCTUATION_RE if cjk else _CREDENTIAL_FULLWIDTH_BOUNDARY_RE
    boundary = boundaries.search(text, start, end)
    if boundary is not None:
        prefix = text[start:boundary.start()]
        if _REDACTION_TOKEN_RE.fullmatch(prefix.rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)):
            # Preserve any ASCII-compatible residual inside the ORIGINAL token,
            # even when it alone is too short to pass a credential validator.
            return end if _has_credential_alnum(text[boundary.end():end]) else boundary.start()
        if validator(prefix):
            return boundary.start()
        if cjk and re.search(_ALNUM, text[boundary.end():end].translate(_FULLWIDTH_ASCII_TRANSLATION)) is None:
            return boundary.start()
    return end


def _credential_matches(text: str, category: str | None = None) -> list[tuple[int, int, str]]:
    """All credential occurrences, validating raw values and masking whole tokens."""
    spans: list[tuple[int, int, str]] = []
    query_matches = list(_URL_CREDENTIAL_RE.finditer(text))
    for match in query_matches:
        cls = PASSWORD if match.group(1).lower() in _URL_PASSWORD_KEYS else API_SECRET
        if category is not None and cls != category:
            continue
        if _concrete_value_ok(match.group(2), min_len=4 if cls == PASSWORD else 8):
            spans.append((*match.span(2), cls))
    groups = (
        (_PASSWORD_STRONG_RE, PASSWORD, _concrete_value_ok),
        (_PASSWORD_COPULA_RE, PASSWORD, _english_copula_value_ok),
        (_PASSWORD_COPULA_CJK_RE, PASSWORD, lambda v: _concrete_value_ok(v) and _secret_shaped(v)),
        (_API_RE[:-1], API_SECRET, lambda v: _concrete_value_ok(v, min_len=8)),
        (_BEARER_RE, API_SECRET, lambda v: _concrete_value_ok(v, min_len=16)),
    )
    for patterns, cls, validator in groups:
        if category is not None and cls != category:
            continue
        for pattern in patterns:
            for match in _iter_matches(pattern, text):
                # Query values have their own category and &/# boundaries.
                if any(query.start() <= match.start() < query.end() for query in query_matches):
                    continue
                start, end = match.span(1)
                cjk = _HAN_KANA_RE.search(text, match.start(), start) is not None
                end = _credential_value_end(text, start, end, validator, cjk=cjk)
                value = text[start:end]
                # A user-typed marker followed by another credential-shaped token
                # cannot hide that token behind the placeholder exemption.
                if _REDACTION_TOKEN_RE.fullmatch(value.rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)):
                    cursor = end
                    while following := _CREDENTIAL_CONTINUATION_RE.match(text, cursor):
                        token_start = following.start(1)
                        token_end = _credential_value_end(text, token_start, following.end(1), validator, cjk=cjk)
                        token = text[token_start:token_end]
                        # Skip standalone separator tokens, but keep punctuation
                        # attached to a value (!!!abc is a valid 6-char secret).
                        if all(unicodedata.category(ch)[0] in {"P", "S"} for ch in token):
                            cursor = token_end
                            continue
                        if _REDACTION_TOKEN_RE.fullmatch(token.rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)):
                            cursor = token_end
                            continue
                        # Yield to a valid value or an already masked one; an
                        # incomplete cue cannot hide a valid continuation.
                        if any((cue := pattern.match(text, token_start))
                               and (cue_validator(cue.group(1)) or _REDACTION_VALUE_RE.fullmatch(
                                   cue.group(1).rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)))
                               for cue_patterns, _, cue_validator in groups for pattern in cue_patterns):
                            break
                        # Check the original value including the marker prefix;
                        # ordinary CJK prose has no ASCII-compatible credential evidence.
                        candidate = text[start:token_end]
                        if (_concrete_value_ok(token, min_len=8 if cls == API_SECRET else 4)
                                and validator(candidate)):
                            end = token_end
                            value = candidate
                        break
                if validator(value):
                    trimmed = value.rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)
                    spans.append((start, start + len(trimmed), cls))
    if category in (None, API_SECRET):
        for match in _iter_matches(_API_RE[-1], text):
            if any(query.start() <= match.start() < query.end() for query in query_matches):
                continue
            # Known bare key shapes can be followed by punctuation inside the
            # same credential value. Keep that suffix inside the masked span.
            tail = re.match(r"\S*", text[match.end():])
            end = match.end() + tail.end()
            shortened = _credential_value_end(
                text, match.start(), end, lambda v: _concrete_value_ok(v, min_len=8),
            )
            # A bare key has no cue left after replacement. Keep ASCII-compatible
            # suffixes covered instead of relying on a cue-based post-check.
            if not _has_credential_alnum(text[shortened:end]):
                end = shortened
            value = text[match.start():end].rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)
            spans.append((match.start(), match.start() + len(value), API_SECRET))
    return spans


def _has_password(text: str) -> bool:
    return bool(_credential_matches(text, PASSWORD))


def _has_api_secret(text: str) -> bool:
    return bool(_credential_matches(text, API_SECRET))


def _otp_value_ok(value: str) -> bool:
    if value.isdigit():
        return 4 <= len(value) <= 8
    if re.fullmatch(r"\d[A-Za-z]{4,}", value):
        return False
    return 4 <= len(value) <= 10 and any(c.isdigit() for c in value)


def _has_otp(text: str) -> bool:
    for pattern in _OTP_RE:
        for m in _iter_matches(pattern, text):
            if _otp_value_ok(m.group(1)):
                return True
    return False


def _luhn_valid(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _has_payment_card(text: str) -> bool:
    if _PAN_LABELED_CN.search(text) or _PAN_LABELED_EN.search(text):
        return True
    for m in _PAN_RUN_RE.finditer(text):
        digits = re.sub(r"[\s-]", "", m.group(0))
        if _luhn_valid(digits):
            return True
    return False


def _prc_id_valid(token: str) -> bool:
    if len(token) != 18:
        return False
    body = token[:17]
    if not body.isdigit():
        return False
    try:
        year, month, day = (
            int(token[6:10]),
            int(token[10:12]),
            int(token[12:14]),
        )
        date(year, month, day)
    except ValueError:
        return False
    total = sum(int(ch) * w for ch, w in zip(body, _PRC_WEIGHTS))
    return token[17].upper() == _PRC_CODES[total % 11]


def _has_government_id(text: str) -> bool:
    if _SSN_RE.search(text):
        return True
    for m in _PRC_ID_RE.finditer(text):
        if _prc_id_valid(m.group(1)):
            return True
    if _GOV_LABELED_CN_ID.search(text) or _GOV_LABELED_CN_PASSPORT.search(text):
        return True
    for m in _iter_matches(_GOV_LABELED_EN, text):
        if _identifier_shaped(m.group(1)):
            return True
    return False


def _has_precise_home_address(text: str) -> bool:
    # P1-C: the residence cue and the precise street/unit information must be
    # coupled inside the SAME clause. A cue in clause A can never authorize an
    # unrelated precise address in clause B (sentence/clause boundaries:
    # ，,。.；;！!？? and newline).
    for m in _PRECISION_CN_RE.finditer(text):
        if _RES_CN_RE.search(_clause_before(text, m.start())):
            return True
    for m in _EN_STREET_RE.finditer(text):
        if _RES_EN_RE.search(_clause_before(text, m.start())):
            return True
    return False


def classify_memory_sensitive_text(text: str) -> frozenset[str]:
    """Classify a whole message into sensitive class names (empty = safe).

    The caller masks located spans, then classifies again and fails closed
    if any sensitive class remains. Returned data contains class names only.
    """
    if not text:
        return frozenset()
    # Only width-fold ASCII, one source character per matching character.
    # Credential parsing keeps raw punctuation and its established shape rules.
    narrowed = text.translate(_FULLWIDTH_ASCII_TRANSLATION)
    out: set[str] = set()
    for cls, detector in (
        (PASSWORD, _has_password),
        (API_SECRET, _has_api_secret),
        (OTP, _has_otp),
        (PAYMENT_CARD, _has_payment_card),
        (GOVERNMENT_ID, _has_government_id),
        (PRECISE_HOME_ADDRESS, _has_precise_home_address),
    ):
        candidate = narrowed if cls in (OTP, PAYMENT_CARD, GOVERNMENT_ID) else text
        if detector(candidate):
            out.add(cls)
    return frozenset(out)


_REDACTION_MARKER_RE = re.compile(r"\[REDACTED(?::|\])", re.IGNORECASE)


def contains_redaction_marker(value: str) -> bool:
    return _REDACTION_MARKER_RE.search(value or "") is not None


def find_memory_sensitive_matches(text: str) -> list[tuple[int, int, str]]:
    """Internal, ephemeral (start, end, category); never return raw values.

    Enumerate every match for coverage verification. Credential values retain
    interior punctuation; only trailing sentence punctuation is excluded.
    Addresses still mask the residence clause, including street/unit data.
    """
    spans = _credential_matches(text)
    # Width folding never changes offsets, so jobs can mask the original text.
    narrowed = text.translate(_FULLWIDTH_ASCII_TRANSLATION)
    def add_values(patterns, category, validator):
        for pattern in patterns:
            for match in _iter_matches(pattern, narrowed):
                start, end = match.span(1)
                boundary = _CLAUSE_BOUNDARY_RE.search(narrowed, start, end)
                if boundary:
                    end = boundary.start()
                if validator(narrowed[start:end]):
                    spans.append((start, end, category))
    add_values(_OTP_RE, OTP, _otp_value_ok)
    for pattern in (_PAN_LABELED_CN, _PAN_LABELED_EN):
        for match in _iter_matches(pattern, narrowed):
            spans.append((*match.span(), PAYMENT_CARD))
    for match in _PAN_RUN_RE.finditer(narrowed):
        if _luhn_valid(re.sub(r"[\s-]", "", match.group())):
            spans.append((*match.span(), PAYMENT_CARD))
    for match in _SSN_RE.finditer(narrowed):
        spans.append((*match.span(), GOVERNMENT_ID))
    add_values([_PRC_ID_RE], GOVERNMENT_ID, _prc_id_valid)
    add_values([_GOV_LABELED_CN_ID, _GOV_LABELED_CN_PASSPORT], GOVERNMENT_ID, lambda v: bool(v))
    add_values([_GOV_LABELED_EN], GOVERNMENT_ID, _identifier_shaped)
    for precision, residence in ((_PRECISION_CN_RE, _RES_CN_RE), (_EN_STREET_RE, _RES_EN_RE)):
        for match in precision.finditer(text):
            head = _clause_before(text, match.start())
            cue = residence.search(head)
            if cue:
                start = match.start() - len(head) + cue.start()
                boundary = _CLAUSE_BOUNDARY_RE.search(text, match.end())
                spans.append((start, boundary.start() if boundary else len(text), PRECISE_HOME_ADDRESS))
    return sorted(spans)


def sensitive_spans(text: str) -> list[tuple[int, int, str]]:
    """Build the replacement plan; jobs verifies it against all original matches."""
    return find_memory_sensitive_matches(text)
