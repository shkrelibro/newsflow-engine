"""Title normalisation and near-duplicate clustering."""
from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")
_STOP = {
    "the", "a", "an", "and", "of", "to", "in", "for", "on", "at", "by", "with", "och", "att", "i", "på", "för",
    "av", "till", "der", "die", "das", "und", "von", "für", "mit", "de", "la", "el", "los", "las", "y", "en",
    "del", "il", "lo", "le", "e", "di", "da", "per", "con", "het", "een", "van", "og", "til", "ja", "την",
    "της", "το", "τα", "και", "για", "με", "στην", "στο", "του", "w", "z", "na", "do", "i", "és", "az",
}


def title_key(title: str, source_name: str = "") -> str:
    t = title or ""
    if source_name and t.lower().endswith(" - " + source_name.lower()):
        t = t[: -(len(source_name) + 3)]
    # drop a trailing " - Outlet" / " | Outlet" chunk (Google News style) when it looks like an outlet name
    m = re.search(r"\s+[\-|]\s+([^\-|]{2,40})$", t)
    if m and len(m.group(1).split()) <= 4 and not re.search(r"\d", m.group(1)):
        t = t[: m.start()]
    t = unicodedata.normalize("NFKC", t).lower()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def tokens(title_key_text: str) -> set[str]:
    return {w for w in title_key_text.split() if len(w) > 2 and w not in _STOP}


def similar(key_a: str, key_b: str, threshold: float = 0.6) -> bool:
    if not key_a or not key_b:
        return False
    if key_a == key_b:
        return True
    ta, tb = tokens(key_a), tokens(key_b)
    if len(ta) < 4 or len(tb) < 4:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    return union > 0 and inter / union >= threshold
