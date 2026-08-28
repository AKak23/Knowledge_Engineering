from __future__ import annotations

import re
import unicodedata


SPACE_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name)
    normalized = SPACE_RE.sub("", normalized)
    return normalized.casefold()
