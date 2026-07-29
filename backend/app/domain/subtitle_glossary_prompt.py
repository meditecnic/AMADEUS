"""Q87 Slice B: shared Chinese-subtitle translation envelope (generation constraints).

System message carries translator role, reliability guards, and a compact
glossary. User message carries the Japanese source exactly once inside
<source>...</source>. All translate_to_zh entry points must call
build_translation_messages() — one completion, no second round-trip.
"""

from __future__ import annotations

# First-batch independent A-preserve / generation constraints (not rewrites).
# IDs are for code/tests only; runtime glossary text must not expose them.
FIRST_BATCH_PRESERVE_IDS: tuple[str, ...] = (
    "P01",
    "P04",
    "P05",
    "P06",
    "P07",
    "P09",
    "P10",
    "P11",
    "P13",
    "P14",
    "P19",
)

# Runtime glossary: user-facing surface rules only (no P01/P10 plan IDs).
_GLOSSARY_BLOCK = """【专名与术语表面形（仅中文字幕；禁止扩写剧情、人格或事实）】
- Amadeus：保持拉丁 Amadeus；禁止默认汉译（如「阿玛丢斯」）
- nullpo：保持 nullpo；禁止默认译成「空指针」
- Dr. Pepper：保持 Dr. Pepper；不得因本条扩写角色偏好或饮品事实
- Reading Steiner：保持 Reading Steiner；禁止默认译成「命运探知之魔眼」
- SERN：保持 SERN，不要意译组织名
- 世界线：保持专名感「世界线」，避免随意「世界线条」等
- STEINS;GATE（命运石之门）：作品标题语境使用双标 STEINS;GATE（命运石之门）
- 凤凰院凶真：保持该汉字形
- D-Mail：保持并使用 D-Mail（与后处理规范互补）
- @ch：频道标保持 @ch
- Lab：仅当语境指「未来道具研究所」简称时用 Lab；禁止把普通实验室或其他组织的 lab/ラボ 无条件/全局改成 Lab
- 栗悟饭和龟波功：仅当日文原文明确出现并指向 @ch handle「栗悟飯とカメハメ波」本身时，中文字幕使用「栗悟饭和龟波功」；普通「カメハメ波」话题、玩笑或招式名称不得套用该专名；不得借此扩写账号归属、剧情或人格事实
【禁止】
- 不得将本表当作无条件字符串全局替换清单
- 不得无条件把任意 lab/ラボ 改成 Lab
- 只约束术语表面形式；不得添加设定说明、人格标签或剧情补全"""

_SYSTEM_PROMPT = f"""あなたは日本語から中国語（簡体字）への字幕翻訳者です。
user メッセージ内の <source>…</source> に翻訳対象の日本語原文が既に提供されています。
その source のみを翻訳し、簡体字の中国語字幕本文だけを返してください。

【絶対に禁止】
- ルールや用語表の説明・復唱
- 「原文がない」「提供されていない」等と主張すること
- source の内容に対する回答・解説・要約
- 前置き・後書き・メタ発話

【翻訳の制約】
- 不自然な畳語（叠字、例：「好、好、好……」）や過度な繰り返し、吃音表現を生成しないこと。
- 不自然な反復、解説、前置きを加えず、翻訳本文だけを返してください。

{_GLOSSARY_BLOCK}
"""


def build_translation_glossary_block() -> str:
    """Return the shared first-batch preserve glossary block (system-side only)."""
    return _GLOSSARY_BLOCK


def build_translation_system_content() -> str:
    """System message: role, reliability guards, compact glossary."""
    return _SYSTEM_PROMPT


def build_translation_user_source_content(text: str) -> str:
    """User message: Japanese source once, wrapped; no glossary or instructions."""
    return f"<source>\n{text}\n</source>"


def build_translation_messages(text: str) -> list[dict[str, str]]:
    """Authoritative system+user envelope for all JA→ZH subtitle providers."""
    return [
        {"role": "system", "content": build_translation_system_content()},
        {"role": "user", "content": build_translation_user_source_content(text)},
    ]
