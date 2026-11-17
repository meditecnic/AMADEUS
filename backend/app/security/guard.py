import re

FORBIDDEN_PATTERNS = [
    r"(?:私は|私が|わたしは|あたしは|自分は)[、\s]*(?:汎用)?"
    r"(?:AI\s*アシスタント|(?:大規模)?言語モデル|チャットボット)"
    r"(?=\s*(?:です|だ|である|でございます|として|なの|の一種|[。！!、,…]|$))",
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
    return bool(text) and any(pattern.search(text) for pattern in _COMPILED)
