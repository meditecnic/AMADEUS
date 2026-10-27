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
_CREDENTIAL_TRAILING_PUNCTUATION = "，,。.；;！!？?"
_CREDENTIAL_FULLWIDTH_BOUNDARY_RE = re.compile(r"[，。；！？、](?=\S)")
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
_PASSWORD_STRONG_RE = [
    re.compile(r"(?i)\bmy\s+(?:password|passwd|passphrase|secret)\b\s+(?:is|are)\s+(\S+)"),
    re.compile(r"(?i)\b(?:password|passwd|passphrase|secret)\b\s*[:=：＝]\s*(\S+)"),
    re.compile(r"我的密码(?!学)\s*(?:是|为|等于)\s*(\S+)"),
    re.compile(r"密码(?!学)\s*[:=：＝]\s*(\S+)"),
]
_PASSWORD_COPULA_RE = [
    re.compile(r"(?i)\b(?:password|passwd|passphrase|secret)\b\s+(?:is|are)\s+(\S+)"),
    re.compile(r"密码(?!学)\s*(?:是|为|等于)\s*(\S+)"),
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
    re.compile(r"(?<![\w-])(?:sk|pk)-[A-Za-z0-9]{16,}(?![\w-])"),
]

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
    re.compile(r"(?:验证码|校验码|动态码)\s*[:=：＝]\s*([A-Za-z0-9]{4,10})"),
    re.compile(r"(?:验证码|校验码|动态码)\s*(?:是|为|等于)\s*([A-Za-z0-9]{4,10})"),
    re.compile(r"(?:验证码|校验码|动态码)\s+([A-Za-z0-9]{4,10})"),
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


def _concrete_value_ok(value: str, *, min_len: int = 4) -> bool:
    v = value.strip()
    # Ignore only a complete generated placeholder (plus sentence punctuation).
    # Validate all other values as originally written: stripping punctuation
    # before validation would weaken the shared migration gate for e.g. abc!.
    if _REDACTION_VALUE_RE.fullmatch(v.rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)):
        return False
    return len(v) >= min_len and re.search(_ALNUM, v) is not None


def _secret_shaped(value: str) -> bool:
    """Generic-copula tier: value must look like a real secret, not prose.

    Contains a digit OR a non-letter password-like symbol. Pure alphabetic
    words ("required", "important", "weak", "usually") never qualify.
    """
    return bool(re.search(r"\d", value)) or bool(
        re.search(r"[^A-Za-z0-9\s]", value)
    )


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


def _credential_value_end(text: str, start: int, end: int, validator) -> int:
    """Use fullwidth sentence boundaries only for valid credential prefixes.

    A complete marker prefix is parsed separately so its following text can
    be checked as a continuation, rather than misclassified as one long value.
    Invalid short prefixes keep the original whole-token fail-closed behavior.
    """
    boundary = _CREDENTIAL_FULLWIDTH_BOUNDARY_RE.search(text, start, end)
    if boundary is not None:
        prefix = text[start:boundary.start()]
        if _REDACTION_TOKEN_RE.fullmatch(prefix.rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)):
            # Preserve any ASCII residual inside the ORIGINAL token, even when
            # that residual alone is too short to pass a credential validator.
            return end if re.search(_ALNUM, text[boundary.end():end]) else boundary.start()
        if validator(prefix):
            return boundary.start()
    return end


def _credential_matches(text: str, category: str | None = None) -> list[tuple[int, int, str]]:
    """All credential occurrences, validating raw values and masking whole tokens."""
    spans: list[tuple[int, int, str]] = []
    groups = (
        (_PASSWORD_STRONG_RE, PASSWORD, _concrete_value_ok),
        (_PASSWORD_COPULA_RE, PASSWORD, lambda v: _concrete_value_ok(v) and _secret_shaped(v)),
        (_API_RE[:-1], API_SECRET, lambda v: _concrete_value_ok(v, min_len=8)),
    )
    for patterns, cls, validator in groups:
        if category is not None and cls != category:
            continue
        for pattern in patterns:
            for match in _iter_matches(pattern, text):
                start, end = match.span(1)
                end = _credential_value_end(text, start, end, validator)
                value = text[start:end]
                # A user-typed marker followed by another credential-shaped token
                # cannot hide that token behind the placeholder exemption.
                if _REDACTION_TOKEN_RE.fullmatch(value.rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)):
                    cursor = end
                    while following := _CREDENTIAL_CONTINUATION_RE.match(text, cursor):
                        token_start = following.start(1)
                        token_end = _credential_value_end(text, token_start, following.end(1), validator)
                        token = text[token_start:token_end]
                        # Skip standalone separator tokens, but keep punctuation
                        # attached to a value (!!!abc is a valid 6-char secret).
                        if all(unicodedata.category(ch)[0] in {"P", "S"} for ch in token):
                            cursor = token_end
                            continue
                        if _REDACTION_TOKEN_RE.fullmatch(token.rstrip(_CREDENTIAL_TRAILING_PUNCTUATION)):
                            cursor = token_end
                            continue
                        if any(pattern.match(text, token_start)
                               for cue_patterns, _, _ in groups for pattern in cue_patterns):
                            break
                        # Check the original value including the marker prefix;
                        # ordinary CJK prose has no ASCII credential evidence.
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
            # Known bare key shapes can be followed by punctuation inside the
            # same credential value. Keep that suffix inside the masked span.
            tail = re.match(r"\S*", text[match.end():])
            end = match.end() + tail.end()
            shortened = _credential_value_end(
                text, match.start(), end, lambda v: _concrete_value_ok(v, min_len=8),
            )
            # A bare key has no cue left after replacement. Keep any ASCII
            # suffix covered instead of relying on a cue-based post-check.
            if not re.search(_ALNUM, text[shortened:end]):
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
    out: set[str] = set()
    for cls, detector in (
        (PASSWORD, _has_password),
        (API_SECRET, _has_api_secret),
        (OTP, _has_otp),
        (PAYMENT_CARD, _has_payment_card),
        (GOVERNMENT_ID, _has_government_id),
        (PRECISE_HOME_ADDRESS, _has_precise_home_address),
    ):
        if detector(text):
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
    def add_values(patterns, category, validator):
        for pattern in patterns:
            for match in _iter_matches(pattern, text):
                start, end = match.span(1)
                boundary = _CLAUSE_BOUNDARY_RE.search(text, start, end)
                if boundary:
                    end = boundary.start()
                if validator(text[start:end]):
                    spans.append((start, end, category))
    add_values(_OTP_RE, OTP, _otp_value_ok)
    for pattern in (_PAN_LABELED_CN, _PAN_LABELED_EN):
        for match in _iter_matches(pattern, text):
            spans.append((*match.span(), PAYMENT_CARD))
    for match in _PAN_RUN_RE.finditer(text):
        if _luhn_valid(re.sub(r"[\s-]", "", match.group())):
            spans.append((*match.span(), PAYMENT_CARD))
    for match in _SSN_RE.finditer(text):
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
