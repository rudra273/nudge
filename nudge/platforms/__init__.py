"""Built-in platform support that works without any AI.

Each platform registers a checker and knows how to recognise a plain-English request
("Jailer 2 tickets on BookMyShow in Bangalore", "Amul whey protein at 560001").
"""

from __future__ import annotations

import re
from typing import Any

from nudge.platforms import amul, bookmyshow, district  # noqa: F401  (registers checkers)
from nudge.platforms.common import find_city

PLATFORMS = {
    "amul": amul,
    "bookmyshow": bookmyshow,
    "district": district,
}

_FILLER = {
    "tell", "me", "notify", "alert", "ping", "let", "know", "when", "whenever", "if", "as", "soon", "please",
    "watch", "monitor", "track", "check", "for", "on", "at", "in", "of", "to", "via", "from", "and", "or",
    "ticket", "tickets", "booking", "bookings", "book", "open", "opens", "opened", "opening", "are", "is", "get",
    "gets", "go", "goes", "live", "available", "availability", "start", "starts", "started", "release",
    "releases", "released", "advance", "sale", "sales", "show", "shows", "showtimes", "movie", "film",
    "nudge", "i", "can", "it", "they", "new", "up", "out", "now", "become", "becomes", "drop", "drops",
}
_PLATFORM_WORDS = re.compile(r"\b(book\s*my\s*show|bookmyshow|bms|district(?:\.in)?|zomato district|paytm insider)\b", re.I)


def extract_title(text: str, city_phrase: str | None = None) -> str | None:
    """Pull a movie/event title out of a sentence, without AI."""
    quoted = re.search(r"[\"“”']([^\"“”']{2,80})[\"“”']", text)
    if quoted:
        return quoted.group(1).strip()
    cleaned = _PLATFORM_WORDS.sub(" ", text)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"\bevery\s+\d*\s*\w+|\bhourly\b|\bdaily\b|\bonce\b", " ", cleaned, flags=re.I)
    if city_phrase:
        cleaned = re.sub(rf"\b{re.escape(city_phrase)}\b", " ", cleaned, flags=re.I)
    words = re.findall(r"[\w:'&.-]+", cleaned)
    kept = []
    for i, w in enumerate(words):
        nxt = words[i + 1].lower() if i + 1 < len(words) else ""
        # "the" belongs to "The Batman", not to "when the tickets open"
        if w.lower() in _FILLER or (w.lower() == "the" and (not nxt or nxt in _FILLER)):
            continue
        kept.append(w)
    title = " ".join(kept).strip(" .,!?-")
    return title or None


def detect(text: str) -> dict[str, Any] | None:
    """Recognise a platform request and return a watch spec dict, or None."""
    for module in (amul, bookmyshow, district):
        spec = module.parse(text)
        if spec:
            return spec
    return None


__all__ = ["PLATFORMS", "detect", "extract_title", "find_city"]
