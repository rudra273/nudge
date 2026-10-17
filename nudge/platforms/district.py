"""District (district.in, by Zomato): movie bookings by title + city, and event pages.

A movie's city page lists showtimes once bookings open, so "bookings open" = showtimes appear.
"""

from __future__ import annotations

import re
from typing import Any

from nudge.checkers import checker
from nudge.checkers.base import CheckContext, CheckResult
from nudge.checkers.builtin import SHOWTIME_RE
from nudge.platforms.common import city_slug, find_city, slugify, title_words

BASE = "https://www.district.in"
LINK_RE = re.compile(r"/movies/([a-z0-9-]+?)-movie-tickets(?:-in-([a-z0-9-]+?))?-MV(\d+)")


def resolve_movie(ctx: CheckContext, title: str, city: str) -> str | None:
    """Find the District page for a movie in a city, from the public listing pages."""
    slug, words = slugify(title), title_words(title)
    found: dict[str, str] = {}  # movie slug -> MV id
    for url in (f"{BASE}/movies/upcoming-movies-in-{city}", f"{BASE}/movies/"):
        try:
            html = ctx.get(url).text
        except Exception:  # one listing failing shouldn't stop us trying the other
            continue
        for movie_slug, _, mv in LINK_RE.findall(html):
            found.setdefault(movie_slug, mv)
        if slug in found:
            break
    if slug in found:
        best = slug
    else:
        candidates = [s for s in found if all(w in s.split("-") for w in words)]
        if not candidates:
            return None
        best = min(candidates, key=len)
    return f"{BASE}/movies/{best}-movie-tickets-in-{city}-MV{found[best]}"


@checker("district")
def check_district(watch, ctx: CheckContext) -> CheckResult:
    """Alert when a movie has showtimes on District in your city.

    params: movie + city (resolved automatically), or point the watch url at a District movie page.
    Optional: cinema (only alert when this cinema has shows, e.g. "PVR Orion").
    """
    p = watch.params
    city = city_slug(p.get("city") or "", "district") if p.get("city") else None
    url = watch.url if "-MV" in watch.url else ctx.memo.get("url")
    if not url:
        if not (p.get("movie") and city):
            raise ValueError("district check needs params.movie and params.city (or a District movie URL)")
        url = resolve_movie(ctx, p["movie"], city)
        if not url:
            return CheckResult(False, f"\"{p['movie']}\" isn't listed on District for {city} yet")
        ctx.memo["url"] = url
    resp = ctx.get(url, allow_error=True)
    if resp.status == 404:
        ctx.memo.pop("url", None)
        return CheckResult(False, "Movie page not found yet")
    text = resp.soup.get_text(" ", strip=True)
    # Ignore the SEO footer ("Movies Now Showing in …") so other films' info can't trigger an alert.
    text = re.split(r"Where can I watch|Movies Now Showing in", text)[0]
    cinema = p.get("cinema")
    if cinema:
        idx = text.lower().find(cinema.lower())
        if idx < 0:
            return CheckResult(False, f"No shows at {cinema} yet")
        times = _cinema_times(text[idx:])
    else:
        times = SHOWTIME_RE.findall(text)
    times = list(dict.fromkeys(times))
    if times:
        where = f" at {cinema}" if cinema else ""
        title = resp.soup.title.get_text(strip=True).split(" - ")[0] if resp.soup.title else "Bookings"
        return CheckResult(True, f"{title}: bookings open{where}! Shows: {', '.join(times[:8])}", link=url)
    released = re.search(r"Releasing [^.]{3,30}?\d{4}", text)
    return CheckResult(False, f"No showtimes yet{' (' + released.group(0) + ')' if released else ''}", link=url)


_FORMAT_TOKEN = re.compile(r"^(?:[A-Z0-9.+-]{1,10}|AM|PM)$")


def _cinema_times(block: str) -> list[str]:
    """Showtimes listed right after a cinema's name/address, stopping at the next cinema."""
    first = SHOWTIME_RE.search(block[:300])
    if not first:
        return []
    times, rest = [], block[first.start():]
    while rest:
        m = SHOWTIME_RE.match(rest)
        if m:
            times.append(m.group(0))
            rest = rest[m.end():].lstrip()
            continue
        token, _, rest = rest.partition(" ")
        if not _FORMAT_TOKEN.match(token):  # e.g. "Megaplex": the next cinema's name begins
            break
    return times


def parse(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    url = re.search(r"https?://(?:www\.)?district\.in/\S+", text)
    if not url and not re.search(r"\bdistrict\b", lowered):
        return None
    if url:
        link = url.group(0).rstrip(".,;)")
        if "/movies/" in link and "-MV" in link:
            return {"url": link, "check": "district", "params": {}, "name": "District: " + _title_from_url(link)}
        return {"url": link, "check": "booking", "params": {}, "name": "District: " + _title_from_url(link)}
    from nudge.platforms import extract_title

    city = find_city(text)
    title = extract_title(text, city[1] if city else None)
    needs = [n for n, v in (("movie", title), ("city", city)) if not v]
    params: dict[str, Any] = {"movie": title or "", "city": city[0] if city else ""}
    cinema = re.search(r"\bat\s+((?:pvr|inox|cinepolis|cinépolis|miraj|carnival|mukta|movietime)[\w .-]{0,30}?)"
                       r"(?=\s+(?:in|on|every|for|once)\b|[.,!?]|$)", text, re.I)
    if cinema:
        params["cinema"] = cinema.group(1).strip()
        if title:
            params["movie"] = title.replace(params["cinema"], "").strip()
    return {"url": f"{BASE}/movies/", "check": "district", "params": params,
            "name": f"{params['movie'] or 'Movie'} on District" + (f" ({params['city']})" if params["city"] else ""),
            "needs": needs}


def _title_from_url(url: str) -> str:
    m = LINK_RE.search(url)
    slug = m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-(?:buy|book)-tickets.*$|-[a-z]{3}\d{1,2}-\d{4}.*$", "", slug)
    return slug.replace("-", " ").title()
