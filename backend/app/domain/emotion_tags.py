"""Remove body emotion tags before publication, including split stream tags."""

import re

EMO_TAG = re.compile(r"\[EMO:([^\]\r\n]{1,24})\]", re.IGNORECASE)
_PARTIAL_TAG = re.compile(r"\[(?:E(?:M(?:O(?::[^\]\r\n]{0,24})?)?)?)?$", re.IGNORECASE)


class EmotionTagFilter:
    def __init__(self):
        self.pending = ""
        self.spaces = ""
        self.after_tag = False

    def feed(self, delta: str, *, final: bool = False) -> str:
        text = self.pending + delta
        self.pending = ""
        output = []
        index = 0
        while index < len(text):
            if text[index] == "[":
                match = EMO_TAG.match(text, index)
                if match:
                    self.spaces = " " if self.spaces else ""
                    self.after_tag = True
                    index = match.end()
                    continue
                if _PARTIAL_TAG.fullmatch(text[index:]):
                    if not final:
                        self.pending = text[index:]
                    break
            char = text[index]
            if char in " \t":
                self.spaces = " " if self.after_tag else self.spaces + char
            else:
                output.append(self.spaces + char)
                self.spaces = ""
                self.after_tag = False
            index += 1
        if final:
            output.append(self.spaces)
            self.spaces = ""
        return "".join(output)


def strip_emotion_tags(text: str) -> str:
    return EmotionTagFilter().feed(text, final=True)
