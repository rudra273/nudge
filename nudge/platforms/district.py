"""District (district.in, by Zomato): movie bookings by title + city, and event pages.

A movie's page embeds its full schedule as JSON (``__NEXT_DATA__``): the dates that are open,
every cinema and every show with its screen format, seats left and a seat-layout link. That is
what the checker reads, so it can filter by date (first day first show), format, language and
cinema. Pages without that JSON fall back to finding showtimes in the page text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from nudge.checkers import checker
from nudge.checkers.base import CheckContext, CheckResult
from nudge.checkers.builtin import SHOWTIME_RE
from nudge.platforms.common import as_list, city_slug, find_city, match_cinema, slugify, title_words

BASE = "https://www.district.in"
LINK_RE = re.compile(r"/movies/([a-z0-9-]+?)-movie-tickets(?:-in-([a-z0-9-]+?))?-MV(\d+)")
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_EXTRA_PAGES = 8  # per check: (date, language) pages beyond the movie page itself


def find_movies(ctx: CheckContext, title: str, city: str) -> list[str]:
    """District page URLs for movies matching a title in a city, best match first."""
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
        ranked = [slug]
    else:
        ranked = sorted((s for s in found if all(w in s.split("-") for w in words)), key=len)
    return [f"{BASE}/movies/{s}-movie-tickets-in-{city}-MV{found[s]}" for s in ranked]


def resolve_movie(ctx: CheckContext, title: str, city: str) -> str | None:
    """Find the District page for a movie in a city, from the public listing pages."""
    found = find_movies(ctx, title, city)
    return found[0] if found else None


# -- the page's embedded schedule ------------------------------------------------

@dataclass
class Show:
    date: str            # 2026-12-18
    time: str            # 06:00 AM
    when: str            # 2026-12-18T06:00, for sorting
    cinema: str
    cinema_id: str
    chain: str = ""
    fmt: str = ""        # 2D, 3D, IMAX 3D, 4DX...
    extra: str = ""      # premium label + audi name, for format matching
    language: str = ""
    avail: int | None = None
    total: int | None = None
    status: str = ""     # Available, Filling Fast, Almost Full, Sold Out
    book_url: str = ""

    @property
    def sold_out(self) -> bool:
        return self.status.lower() == "sold out" or self.avail == 0


@dataclass
class Schedule:
    shows: list[Show]
    show_dates: list[str]
    release_date: str | None
    groups: dict[str, str]          # format-group id -> language
    covered: set[tuple[str, str]]   # (date, group id) whose shows are in `shows`
    title: str | None = None


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _clock(iso: str) -> str:
    try:
        return datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M").strftime("%I:%M %p")
    except ValueError:
        return iso[11:16]


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_page(html: str, content_id: str = "") -> Schedule | None:
    """Read the schedule District embeds in a movie page. None if the page has no such data."""
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    show_dates: list[str] = []
    release: str | None = None
    title: str | None = None
    groups: dict[str, str] = {}
    session_sets: list[tuple[str, dict]] = []  # (movieSessions key, value)
    for node in _walk(data):
        for d in node.get("showDates") or []:
            if isinstance(d, str) and ISO_DATE_RE.match(d) and d not in show_dates:
                show_dates.append(d)
        for key in ("release_date", "releaseDate"):
            if not release and isinstance(node.get(key), str) and ISO_DATE_RE.match(node[key]):
                release = node[key]
                title = title or node.get("name")
        for g in node.get("languageFormatGroups") or []:
            if isinstance(g, dict) and g.get("format"):
                groups.setdefault(str(g["format"]), str(g.get("language") or ""))
        if isinstance(node.get("movieSessions"), dict):
            session_sets.extend(node["movieSessions"].items())
    show_dates.sort()

    shows: dict[tuple[str, str, str], Show] = {}
    covered: set[tuple[str, str]] = set()
    for key, value in session_sets:
        if not isinstance(value, dict):
            continue
        group = next((g for g in groups if key.startswith(g)), key[:11])
        day = value.get("searchDate") or (show_dates[0] if show_dates else "")
        if day:
            covered.add((day, group))
        page = value.get("pageData") or {}
        for cinema in (page.get("nearbyCinemas") or []) + (page.get("farCinemas") or []):
            info = cinema.get("cinemaInfo") or {}
            for s in cinema.get("sessions") or []:
                when = str(s.get("showTime") or "")
                if not ISO_DATE_RE.match(when[:10]):
                    continue
                gid = str(s.get("mcd") or group)
                book = ""
                if s.get("encSessionId"):
                    book = (f"{BASE}/movies/seat-layout/{gid}?encsessionid={s['encSessionId']}&freeseating=false"
                            f"&fromsessions=true&type=MOVIES&contentid={content_id}")
                show = Show(date=when[:10], time=_clock(when), when=when[:16], cinema=str(info.get("name") or info.get("label") or ""),
                            cinema_id=str(s.get("cid") or cinema.get("id") or ""), chain=str(info.get("chainKey") or ""),
                            fmt=str(s.get("scrnFmt") or ""),
                            extra=" ".join(str(s.get(k) or "") for k in ("premiumLabel", "audi")),
                            language=groups.get(gid, ""), avail=_int(s.get("avail")), total=_int(s.get("total")),
                            status=str(s.get("seatStatus") or ""), book_url=book)
                shows.setdefault((show.cinema_id, show.date, str(s.get("sid") or when)), show)
    return Schedule(list(shows.values()), show_dates, release, groups, covered, title)


def _target_dates(p: dict, schedule: Schedule) -> list[str] | None:
    """Dates the user asked for (None = any). 'release' means the movie's release day."""
    wanted = []
    for d in as_list(p.get("date")):
        if d.lower() in ("release", "fdfs", "first day"):
            if schedule.release_date:
                wanted.append(schedule.release_date)
        elif ISO_DATE_RE.match(d):
            wanted.append(d)
    return wanted if p.get("date") else None


def _wants(show: Show, p: dict) -> bool:
    cinemas = as_list(p.get("cinema"))
    # Only the name: District files INOX cinemas under the "PVR" chain since the merger.
    if cinemas and not any(match_cinema(c, show.cinema) for c in cinemas):
        return False
    formats = [f.lower().replace(" ", "") for f in as_list(p.get("format"))]
    have = f"{show.fmt} {show.extra}".lower().replace(" ", "")
    if formats and not any(f in have for f in formats):
        return False
    languages = [lang.lower() for lang in as_list(p.get("language"))]
    if languages and show.language and not any(show.language.lower().startswith(lang) for lang in languages):
        return False
    return True


def _nice_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%a %d %b")
    except ValueError:
        return iso


def _short_cinema(name: str) -> str:
    parts = [x.strip() for x in name.split(",") if x.strip()]
    return ", ".join(parts[:2]) if len(parts) > 2 else ", ".join(parts[:1] or [name])


def _show_label(s: Show) -> str:
    bits = [s.time]
    if s.fmt and s.fmt.upper() != "2D":
        bits.append(s.fmt)
    label = " ".join(bits)
    if s.sold_out:
        return label + " (sold out)"
    if s.status.lower() in ("filling fast", "almost full"):
        seats = f"{s.avail}/{s.total} left, " if s.avail is not None and s.total else ""
        return f"{label} ({seats}{s.status.lower()})"
    return label


def format_shows(shows: list[Show], max_cinemas: int = 10) -> str:
    by_date: dict[str, dict[str, list[Show]]] = {}
    for s in sorted(shows, key=lambda x: (x.date, x.cinema, x.when)):
        by_date.setdefault(s.date, {}).setdefault(s.cinema, []).append(s)
    lines, shown = [], 0
    for day, cinemas in by_date.items():
        if shown >= max_cinemas:
            break
        lines.append(f"📅 {_nice_date(day)}")
        for cinema, items in cinemas.items():
            if shown >= max_cinemas:
                break
            lines.append(f"• {_short_cinema(cinema)}: {', '.join(_show_label(s) for s in items[:6])}"
                         + (" …" if len(items) > 6 else ""))
            shown += 1
    total = len({(s.date, s.cinema) for s in shows})
    if total > shown:
        lines.append(f"…and {total - shown} more")
    return "\n".join(lines)


def _page_url(url: str, group: str | None = None, day: str | None = None) -> str:
    query = "&".join(f"{k}={v}" for k, v in (("frmtid", group), ("fromdate", day)) if v)
    return f"{url}?{query}" if query else url


def _check_schedule(watch, ctx: CheckContext, url: str, schedule: Schedule, page_title: str) -> CheckResult:
    p = watch.params
    content_id = (re.search(r"-MV(\d+)", url) or [None, ""])[1]
    title = schedule.title or page_title
    if not schedule.show_dates:
        when = f"releasing {_nice_date(schedule.release_date)}" if schedule.release_date else "not open yet"
        return CheckResult(False, f"No shows yet ({when})", link=url)

    targets = _target_dates(p, schedule)
    if targets is not None:
        days = [d for d in targets if d in schedule.show_dates]
        if not days:
            asked = ", ".join(_nice_date(d) for d in targets) if targets else "release day"
            return CheckResult(False, f"Bookings open for {', '.join(_nice_date(d) for d in schedule.show_dates[:4])}, "
                                      f"not yet for {asked}", link=url)
    elif p.get("cinema") or p.get("format") or p.get("language"):
        days = schedule.show_dates[:3]  # a filtered cinema/format may only open on a later day
    else:
        days = schedule.show_dates[:1]  # the page itself shows the first open day
    languages = [lang.lower() for lang in as_list(p.get("language"))]
    groups = [g for g, lang in schedule.groups.items()
              if not languages or any(lang.lower().startswith(x) for x in languages)] or [None]

    shows = list(schedule.shows)
    covered = set(schedule.covered)
    extra = 0
    for day in days:
        for group in groups[:4]:
            if (day, group) in covered or (group is None and any(c[0] == day for c in covered)):
                continue
            if extra >= MAX_EXTRA_PAGES:
                break
            extra += 1
            more = parse_page(ctx.get(_page_url(url, group, day)).text, content_id)
            if more:
                shows.extend(more.shows)
                covered |= more.covered | {(day, group)}
    seen_keys: set[tuple[str, str, str]] = set()
    matching = []
    for s in shows:
        key = (s.cinema_id, s.date, s.time)
        if s.date in days and _wants(s, p) and key not in seen_keys:
            seen_keys.add(key)
            matching.append(s)

    filtered = bool(p.get("cinema") or p.get("format") or p.get("language") or targets)
    if not matching:
        open_days = ", ".join(_nice_date(d) for d in schedule.show_dates[:4])
        what = " matching your filters" if filtered else ""
        return CheckResult(False, f"Bookings open ({open_days}) but no shows{what} yet", link=url)

    bookable = [s for s in matching if not s.sold_out and s.book_url] or matching
    link = min(bookable, key=lambda s: s.when).book_url or url
    units = sorted({f"{s.date}|{s.cinema_id}" for s in matching})
    details = {"dates": sorted({s.date for s in matching}), "cinemas": len({s.cinema_id for s in matching}),
               "shows": len(matching)}

    if p.get("updates", True) is False:
        return CheckResult(True, f"{title}: bookings open!\n{format_shows(matching)}", link=link, details=details)

    seen = set(ctx.memo.get("seen") or [])
    today = date.today().isoformat()
    ctx.memo["seen"] = sorted(u for u in seen | set(units) if u[:10] >= today)
    new = [s for s in matching if f"{s.date}|{s.cinema_id}" not in seen]
    if not new:
        return CheckResult(False, f"Bookings open: {len(details['dates'])} day(s), {details['cinemas']} cinema(s) "
                                  f"(no change)", link=url, edge=False, details=details)
    head = f"{title}: bookings open!" if not seen else f"{title}: more shows open"
    return CheckResult(True, f"{head}\n{format_shows(new)}", link=link, edge=False, details=details)


@checker("district")
def check_district(watch, ctx: CheckContext) -> CheckResult:
    """Alert when a movie has shows on District in your city.

    params: movie + city (resolved automatically), or point the watch url at a District movie page.
    Filters: date (YYYY-MM-DD, a list, or "release" for first day first show), format (IMAX, 4DX, 3D...),
    language, cinema (a name, a chain like "PVR", or a list; matched loosely).
    updates (default true): after the first alert, also alert when new days or cinemas open.
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
    page_title = resp.soup.title.get_text(strip=True).split(" - ")[0] if resp.soup.title else "Bookings"
    page_title = re.sub(r"^Book (.+?) Tickets.*$", r"\1", page_title)
    content_id = (re.search(r"-MV(\d+)", url) or [None, ""])[1]
    schedule = parse_page(resp.text, content_id)
    if schedule is not None and (schedule.show_dates or schedule.release_date or schedule.shows):
        return _check_schedule(watch, ctx, url, schedule, page_title)
    return _check_text(watch, resp, url)


def _check_text(watch, resp, url: str) -> CheckResult:
    """Fallback for pages without the embedded schedule: find showtimes in the page text."""
    p = watch.params
    text = resp.soup.get_text(" ", strip=True)
    # Ignore the SEO footer ("Movies Now Showing in …") so other films' info can't trigger an alert.
    text = re.split(r"Where can I watch|Movies Now Showing in", text)[0]
    cinema = as_list(p.get("cinema"))
    if cinema:
        times: list[str] = []
        for c in cinema:
            idx = text.lower().find(c.lower())
            if idx >= 0:
                times += _cinema_times(text[idx:])
        if not times:
            return CheckResult(False, f"No shows at {' / '.join(cinema)} yet")
    else:
        times = SHOWTIME_RE.findall(text)
    times = list(dict.fromkeys(times))
    if times:
        where = f" at {' / '.join(cinema)}" if cinema else ""
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


CHAINS = (r"pvr|inox|cinepolis|cinépolis|miraj|carnival|mukta|movietime|ags|sathyam|spi|escape|palazzo|luxe|"
          r"vettri|rohini|kasi|gopalan|innovative|urvashi|prasads|amb|asian|allu|cinemax|wave|rajhans|mallikarjuna|"
          r"sudarshan|devi|santosh|vaibhav|mirage|connplex|citypride|e-square|e square")
_CINEMA_STOP = r"(?=\s+(?:in|on|every|for|once|when)\b|[.,!?]|$)"
_CINEMA_RES = (
    re.compile(r"\bat\s+[\"“”]([^\"“”]{2,60})[\"“”]", re.I),
    re.compile(rf"\bat\s+((?:any\s+)?(?:{CHAINS})\b[\w .&/'-]{{0,60}}?){_CINEMA_STOP}", re.I),
)


def _cinemas(text: str, city_phrase: str | None) -> tuple[list[str], str]:
    """Cinema names after "at": quoted, starting with a known chain, or anything right before "in <city>"."""
    found = None
    for rx in _CINEMA_RES:
        found = rx.search(text)
        if found:
            break
    if not found and city_phrase:
        found = re.search(rf"\bat\s+((?:any\s+)?[a-z][\w .&/'-]{{1,60}}?)\s+in\s+{re.escape(city_phrase)}\b", text, re.I)
        if found and re.fullmatch(r"(?:district|bookmyshow|bms|\d+)", found.group(1).strip(), re.I):
            found = None
    if not found:
        return [], text
    names = [re.sub(r"^any\s+", "", n.strip(), flags=re.I) for n in
             re.split(r"\s*(?:,|/|\bor\b|\band\b)\s*", found.group(1), flags=re.I)]
    return [n for n in names if n], text.replace(found.group(0), " ")


def parse(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    url = re.search(r"https?://(?:www\.)?district\.in/\S+", text)
    if not url and not re.search(r"\bdistrict\b", lowered):
        return None
    from nudge.platforms import extract_title
    from nudge.platforms.common import extract_movie_filters

    filters, rest = extract_movie_filters(text)
    if url:
        link = url.group(0).rstrip(".,;)")
        if "/movies/" in link and "-MV" in link:
            cinemas, _ = _cinemas(rest.replace(url.group(0), " "), None)
            params = {**filters, **({"cinema": cinemas[0] if len(cinemas) == 1 else cinemas} if cinemas else {})}
            return {"url": link, "check": "district", "params": params, "name": "District: " + _title_from_url(link)}
        return {"url": link, "check": "booking", "params": {}, "name": "District: " + _title_from_url(link)}

    city = find_city(rest)
    cinemas, rest = _cinemas(rest, city[1] if city else None)
    city = find_city(rest) or city
    title = extract_title(rest, city[1] if city else None)
    needs = [n for n, v in (("movie", title), ("city", city)) if not v]
    params: dict[str, Any] = {"movie": title or "", "city": city[0] if city else ""}
    if cinemas:
        params["cinema"] = cinemas[0] if len(cinemas) == 1 else cinemas
    params.update(filters)
    tags = []
    if params.get("date") == "release":
        tags.append("FDFS")
    elif params.get("date"):
        tags.append(", ".join(_nice_date(d) for d in as_list(params["date"])))
    tags += as_list(params.get("format")) + as_list(params.get("language"))
    name = (params["movie"] or "Movie") + (f" [{' · '.join(tags)}]" if tags else "") + " on District"
    return {"url": f"{BASE}/movies/", "check": "district", "params": params,
            "name": name + (f" ({params['city']})" if params["city"] else ""), "needs": needs}


def _title_from_url(url: str) -> str:
    m = LINK_RE.search(url)
    slug = m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-(?:buy|book)-tickets.*$|-[a-z]{3}\d{1,2}-\d{4}.*$", "", slug)
    return slug.replace("-", " ").title()
