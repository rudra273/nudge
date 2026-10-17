from __future__ import annotations

import re

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
