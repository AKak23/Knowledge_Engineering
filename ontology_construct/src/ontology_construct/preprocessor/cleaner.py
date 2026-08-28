from __future__ import annotations

import re


CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")
MANY_BLANK_LINES_RE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = CONTROL_CHARS_RE.sub("", cleaned)
    cleaned = TRAILING_SPACE_RE.sub("\n", cleaned)
    cleaned = MANY_BLANK_LINES_RE.sub("\n\n", cleaned)
    return cleaned.strip()
