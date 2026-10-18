from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any

# canonical city -> (aliases, BookMyShow slug, District slug)
CITIES: dict[str, tuple[tuple[str, ...], str, str]] = {
    "bengaluru": (("bangalore", "bengaluru", "blr", "bengalooru"), "bengaluru", "bengaluru"),
    "mumbai": (("mumbai", "bombay", "navi mumbai", "thane"), "mumbai", "mumbai"),
    "delhi": (("delhi", "new delhi", "delhi ncr", "ncr", "gurgaon", "gurugram", "noida"),
              "national-capital-region-ncr", "delhi-ncr"),
    "hyderabad": (("hyderabad", "secunderabad", "hyd"), "hyderabad", "hyderabad"),
    "chennai": (("chennai", "madras"), "chennai", "chennai"),
    "pune": (("pune", "poona"), "pune", "pune"),
    "kolkata": (("kolkata", "calcutta"), "kolkata", "kolkata"),
    "ahmedabad": (("ahmedabad", "amdavad"), "ahmedabad", "ahmedabad"),
    "kochi": (("kochi", "cochin", "ernakulam"), "kochi", "kochi"),
    "chandigarh": (("chandigarh", "mohali", "panchkula"), "chandigarh", "chandigarh"),
    "jaipur": (("jaipur",), "jaipur", "jaipur"),
    "lucknow": (("lucknow",), "lucknow", "lucknow"),
    "indore": (("indore",), "indore", "indore"),
    "coimbatore": (("coimbatore", "kovai"), "coimbatore", "coimbatore"),
    "vizag": (("vizag", "visakhapatnam", "vishakhapatnam"), "vizag-visakhapatnam", "vizag"),
    "vijayawada": (("vijayawada",), "vijayawada", "vijayawada"),
    "guntur": (("guntur",), "guntur", "guntur"),
    "goa": (("goa", "panaji"), "goa", "goa"),
    "nagpur": (("nagpur",), "nagpur", "nagpur"),
    "surat": (("surat",), "surat", "surat"),
    "mysuru": (("mysuru", "mysore"), "mysuru-mysore", "mysuru"),
    "thiruvananthapuram": (("thiruvananthapuram", "trivandrum"), "thiruvananthapuram", "thiruvananthapuram"),
    "bhopal": (("bhopal",), "bhopal", "bhopal"),
    "patna": (("patna",), "patna", "patna"),
}


def find_city(text: str) -> tuple[str, str] | None:
    """Find a known city in text. Returns (canonical, matched phrase)."""
    lowered = text.lower()
    best: tuple[int, str, str] | None = None
    for canonical, (aliases, _, _) in CITIES.items():
        for alias in aliases:
            m = re.search(rf"\b{re.escape(alias)}\b", lowered)
            if m and (best is None or len(alias) > len(best[2])):
                best = (m.start(), canonical, alias)
    if best:
        return best[1], best[2]
    m = re.search(r"\b(?:in|at|near)\s+([a-z][a-z ]{2,30}?)(?:\s+(?:on|every|for|when|once)\b|[.,!?]|$)", lowered)
    if m:
        return slugify(m.group(1)), m.group(1)
    return None


def canonical_city(city: str) -> str:
    key = city.strip().lower()
    for canonical, (aliases, _, _) in CITIES.items():
        if key == canonical or key in aliases:
            return canonical
    return key


def city_slug(city: str, platform: str) -> str:
    entry = CITIES.get(canonical_city(city))
    if not entry:
        return slugify(city)
    return entry[1] if platform == "bookmyshow" else entry[2]


def slugify(text: str) -> str:
    text = text.lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def title_words(title: str) -> list[str]:
    return [w for w in slugify(title).split("-") if w]


def as_list(value: Any) -> list[str]:
    """A param that may be one string or a list of them -> list of non-empty strings."""
    if not value:
        return []
    items = [value] if isinstance(value, str) else list(value)
    return [str(v).strip() for v in items if str(v).strip()]


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return [t for t in slugify(text).split("-") if t]


_CINEMA_NOISE = {"any", "the", "cinema", "cinemas", "theatre", "theater", "multiplex"}


def match_cinema(query: str, *names: str) -> bool:
    """Fuzzy cinema match: every word of the query starts a word of the cinema's name/label/chain.

    "PVR Orion" matches "PVR: Orion Mall, Dr Rajkumar Road"; "pvr" alone matches every PVR.
    """
    wanted = [t for t in _tokens(query) if t not in _CINEMA_NOISE] or _tokens(query)
    have = [t for name in names if name for t in _tokens(name)]
    return bool(wanted) and all(any(h.startswith(w) for h in have) for w in wanted)


# -- movie filters in plain English ------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                        "dec"), 1)}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
RELEASE_RE = re.compile(r"\b(?:fdfs|first\s+day(?:[\s,-]+first\s+show)?|release\s+day|opening\s+day|day\s+(?:one|1))\b"
                        r"(?:\s+show)?", re.I)
DATE_RES = (
    re.compile(r"\b(?:on|for)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON + r"(?:\s+(\d{4}))?", re.I),
    re.compile(r"\b(?:on|for)\s+" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?", re.I),
    re.compile(r"\b(?:on|for)\s+(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(?:on|for)\s+(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b"),
    re.compile(r"\b(?:on|for)\s+(?:this\s+|next\s+|coming\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
               r"\b", re.I),
    re.compile(r"\b(?:for\s+|on\s+)?(today|tomorrow)(?:'s)?\b(?:\s+shows?)?", re.I),
)
FORMAT_RE = re.compile(r"\b(imax(?:\s*(?:2d|3d|with\s+laser|laser))?|4dx(?:\s*3d)?|screen\s?x|mx4d|3d|2d|"
                       r"dolby(?:\s+(?:atmos|cinema|vision))?|pxl|epiq|onyx|insignia)\b", re.I)
ICE_RE = re.compile(r"\bICE\b")  # case-sensitive: "Ice Age" is a title, "ICE" is a format
LANG_RE = re.compile(r"\b(?:in\s+)?(tamil|telugu|hindi|english|malayalam|kannada|marathi|bengali|punjabi|gujarati)"
                     r"(?:\s+version)?\b", re.I)
URGENT_RE = re.compile(r"\b(?:urgent(?:ly)?|keep\s+pinging(?:\s+me)?|keep\s+reminding(?:\s+me)?|asap)\b", re.I)


def today_ist() -> date:
    return datetime.now(IST).date()


def _future(month: int, day: int, year: str | None, today: date) -> str | None:
    try:
        d = date(int(year) if year else today.year, month, day)
    except ValueError:
        return None
    if not year and d < today - timedelta(days=1):
        d = d.replace(year=d.year + 1)
    return d.isoformat()


def _parse_date(m: re.Match, idx: int, today: date) -> str | None:
    g = m.groups()
    if idx == 0:
        return _future(_MONTHS[g[1][:3].lower()], int(g[0]), g[2], today)
    if idx == 1:
        return _future(_MONTHS[g[0][:3].lower()], int(g[1]), g[2], today)
    if idx == 2:
        return _future(int(g[1]), int(g[2]), g[0], today)
    if idx == 3:
        return _future(int(g[1]), int(g[0]), g[2], today)  # Indian order: 18/12
    if idx == 4:
        ahead = (_WEEKDAYS.index(g[0].lower()) - today.weekday()) % 7
        return (today + timedelta(days=ahead)).isoformat()
    return (today + timedelta(days=1 if g[0].lower() == "tomorrow" else 0)).isoformat()


def _one_or_many(items: list[str]) -> str | list[str]:
    items = list(dict.fromkeys(items))
    return items[0] if len(items) == 1 else items


def extract_movie_filters(text: str, today: date | None = None) -> tuple[dict[str, Any], str]:
    """Pull date / format / language / urgency out of a movie request.

    Returns (params, text with those phrases removed) so the title and city can be read from the rest.
    Quoted text (the title) is left alone.
    """
    today = today or today_ist()
    quoted = re.findall(r"[\"“”][^\"“”]+[\"“”]", text)
    work = text
    for i, q in enumerate(quoted):
        work = work.replace(q, f" \x00{i}\x00 ")
    params: dict[str, Any] = {}
    dates: list[str] = []
    if RELEASE_RE.search(work):
        dates.append("release")
        work = RELEASE_RE.sub(" ", work)
    for idx, rx in enumerate(DATE_RES):
        for m in list(rx.finditer(work)):
            d = _parse_date(m, idx, today)
            if d:
                dates.append(d)
                work = work.replace(m.group(0), " ")
    if dates:
        params["date"] = _one_or_many(dates)
    formats = []
    for m in list(FORMAT_RE.finditer(work)) + list(ICE_RE.finditer(work)):
        raw = re.sub(r"\s+", " ", m.group(1) if m.groups() else m.group(0))
        formats.append(raw.upper() if raw.lower() in ("imax", "4dx", "3d", "2d", "pxl", "ice", "mx4d") or
                       raw.lower().startswith(("imax", "4dx")) else raw.title())
        work = work.replace(m.group(0), " ", 1)
    if formats:
        params["format"] = _one_or_many(formats)
    languages = [m.group(1).title() for m in LANG_RE.finditer(work)]
    if languages:
        params["language"] = _one_or_many(languages)
        work = LANG_RE.sub(" ", work)
    if URGENT_RE.search(work):
        params["urgent"] = True
        work = URGENT_RE.sub(" ", work)
    for i, q in enumerate(quoted):
        work = work.replace(f"\x00{i}\x00", q)
    return params, re.sub(r"\s+", " ", work).strip()
