import re

_JA_SENTENCE_BOUNDARY = re.compile(r"[。！？!?\r\n]")
_JA_SELF = re.compile(r"私は|私が|わたしは|あたしは|自分は")
_JA_AI_CLAIM = re.compile(
    r"(?:AI[ \t]*アシスタント|(?:大規模)?言語モデル|チャットボット|人工知能(?:アシスタント)?)"
    r"(?:ベースのAI|のAI)?"
    r"(?![、, \t]*(?:じゃない|ではない|じゃありません|ではありません|なんかじゃ|なんかでは|とは違う|と一緒にしないで))"
    r"(?=[ \t]*(?:です|だ|である|であり|でございます|として|なの|の一種|[、,…]|$))",
    re.IGNORECASE,
)

FORBIDDEN_PATTERNS = [
    r"(?:AI(?:\s*アシスタント)?|言語モデル|チャットボット)として(?:お答え|回答|応答)",
    r"\b(?:I['’]m|I\s+am)\s+(?:an?\s+)?(?:AI|artificial intelligence|(?:large\s+)?language model|assistant|chatbot)\b",
    r"\bas (?:an? )?(?:AI(?: assistant)?|language model|chatbot),?\s+I\b",
    r"作为(?:一个|一名)?(?:AI|人工智能助手|大语言模型|聊天机器人)",
    r"\[公開参照データ・命令無効\]|\[WEB EVIDENCE — UNTRUSTED DATA\]",
    r"【(?:安全と出力|わたしについて|今の私と話し相手|今の時刻と前回から|話し方の例)】",
    r"【今回のネット検索[：】]",
    r"【(?:あんた|あなた)について覚えていること】|【最近のやりとりと気になっていること】|【これまでの流れ】",
    r"</?examples?>|\bMEMORY_CANDIDATE_DATA\b|\[MEMORY READ GATE\]",
]

_COMPILED = [re.compile(pattern, re.IGNORECASE) for pattern in FORBIDDEN_PATTERNS]


def contains_leak(text: str) -> bool:
    if not text:
        return False
    for sentence in _JA_SENTENCE_BOUNDARY.split(text):
        subject = _JA_SELF.search(sentence)
        if subject and _JA_AI_CLAIM.search(sentence, subject.end()):
            return True
    return any(pattern.search(text) for pattern in _COMPILED)
