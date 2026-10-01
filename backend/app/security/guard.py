# backend/app/security/guard.py
import re

FORBIDDEN_PATTERNS = [
    r"AIアシスタント", r"AI\s*アシスタント",
    r"言語モデル", r"大規?模言語モデル",
    r"アシスタントとして", r"アシスタントです",
    r"チャットボット", r"ボットです",
    r"\b(I['’]m|I\s+am)\s+(an?\s+)?(AI|artificial intelligence|language model|assistant|chatbot)\b",
    r"\bas (an )?(AI|assistant|language model|chatbot)\b",
    r"\bchatbot\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in FORBIDDEN_PATTERNS]

def contains_leak(text: str) -> bool:
    if not text:
        return False
    # Strip any emotion tag before checking
    clean_text = re.sub(r'^\[(?:E?MO)[:=]([a-zA-Z0-9_]+)\]\s*', '', text)
    return any(p.search(clean_text) for p in _COMPILED)
