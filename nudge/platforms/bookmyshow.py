"""BookMyShow: movie bookings by title + city, and any BookMyShow event/movie page.

BookMyShow sits behind Cloudflare and often blocks datacenter IPs (cloud servers, GitHub
Actions). From a home connection it usually works; browser mode (NUDGE_BROWSER=1) helps more.
When blocked, Nudge reports an error instead of guessing.
"""

from __future__ import annotations

import re
from typing import Any

from nudge.checkers import checker
from nudge.checkers.base import CheckContext, CheckResult
from nudge.checkers.builtin import booking_state
from nudge.platforms.common import city_slug, find_city, slugify, title_words

BASE = "https://in.bookmyshow.com"
BUY_RE = re.compile(r"/buytickets/[a-z0-9-]+/movie-[a-z0-9-]+-(ET\d{6,})", re.I)


def _find_movie(html: str, title: str) -> tuple[str, str] | None:
    """Find (slug, ET code) for a title among BookMyShow movie links in a page."""
    slug, words = slugify(title), title_words(title)
    found: dict[str, str] = {}
    for m in re.finditer(r"/movies/(?:[a-z0-9-]+/)?([a-z0-9-]+)/(ET\d{6,})", html):
        found.setdefault(m.group(1), m.group(2))
    if slug in found:
        return slug, found[slug]
    candidates = [s for s in found if all(w in s.split("-") for w in words)]
    if candidates:
        best = min(candidates, key=len)
        return best, found[best]
    return None


@checker("bookmyshow")
def check_bookmyshow(watch, ctx: CheckContext) -> CheckResult:
    """Alert when a movie's bookings open on BookMyShow in your city.

    params: movie + city, or point the watch url at a BookMyShow movie/event page.
    """
    p = watch.params
    if "/ET" in watch.url:
        resp = ctx.fetch(watch)
        if BUY_RE.search(resp.text) or "/buytickets/" in resp.text:
            return CheckResult(True, "Bookings are open on BookMyShow!", link=watch.url)
        is_open, reason = booking_state(resp)
        return CheckResult(is_open, f"Bookings are open: {reason}" if is_open else reason, link=watch.url)

    if not (p.get("movie") and p.get("city")):
        raise ValueError("bookmyshow check needs params.movie and params.city (or a BookMyShow page URL)")
    city = city_slug(p["city"], "bookmyshow")
    now_showing = ctx.get(f"{BASE}/explore/movies-{city}")
    hit = _find_movie(now_showing.text, p["movie"])
    if hit:
        link = f"{BASE}/movies/{city}/{hit[0]}/{hit[1]}"
        return CheckResult(True, f"\"{p['movie']}\" is now booking in {p['city'].title()} on BookMyShow!", link=link)
    try:
        upcoming = _find_movie(ctx.get(f"{BASE}/explore/upcoming-movies-{city}").text, p["movie"])
    except Exception:  # the upcoming list is only for a nicer status message
        upcoming = None
    if upcoming:
        return CheckResult(False, f"\"{p['movie']}\" is listed as upcoming; bookings not open yet",
                           link=f"{BASE}/movies/{city}/{upcoming[0]}/{upcoming[1]}")
    return CheckResult(False, f"\"{p['movie']}\" isn't booking in {p['city'].title()} yet")


def parse(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    url = re.search(r"https?://(?:in\.|www\.)?bookmyshow\.com/\S+", text)
    if not url and not re.search(r"\b(book\s*my\s*show|bookmyshow|bms)\b", lowered):
        return None
    if url:
        link = url.group(0).rstrip(".,;)")
        name = link.rstrip("/").split("/")[-2] if "/ET" in link else link.rstrip("/").rsplit("/", 1)[-1]
        return {"url": link, "check": "bookmyshow", "params": {},
                "name": "BookMyShow: " + name.replace("-", " ").title()}
    from nudge.platforms import extract_title

    city = find_city(text)
    title = extract_title(text, city[1] if city else None)
    needs = [n for n, v in (("movie", title), ("city", city)) if not v]
    params = {"movie": title or "", "city": city[0] if city else ""}
    return {"url": f"{BASE}/explore/movies-{city_slug(params['city'], 'bookmyshow') if city else 'bengaluru'}",
            "check": "bookmyshow", "params": params,
            "name": f"{title or 'Movie'} on BookMyShow" + (f" ({params['city']})" if city else ""), "needs": needs}
