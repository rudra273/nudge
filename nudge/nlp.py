"""Turn plain English into a watch.

Rules come first and need no AI: built-in platforms (BookMyShow, District, Amul) and generic
URL watches. If an AI provider is configured (OpenAI, Gemini, Claude, Ollama...), it handles
requests the rules can't understand, or everything when the user asks with /ai.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from nudge.models import Watch, format_duration, new_id, parse_duration

if TYPE_CHECKING:
    from nudge.llm import LLMConfig

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.I)
QUOTED_RE = re.compile(r"[\"“”']([^\"“”']{1,200})[\"“”']")
BACKTICK_RE = re.compile(r"`([^`]+)`")
EVERY_RE = re.compile(
    r"\bevery\s+(\d+(?:\.\d+)?)?\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|weeks?)\b", re.I)
CRON_RE = re.compile(r"\bcron\s+[\"'`]?([\d*/,\-]+(?:\s+[\d*/,\-A-Za-z]+){4})[\"'`]?", re.I)
FIELD_RE = re.compile(
    r"\b(?:field|key|json(?: path)?)\s+([\w.\[\]-]+)\s+"
    r"(exists|is missing|missing|is true|is truthy|is not|!=|==|=|>=|<=|>|<|is above|is below|"
    r"above|below|over|under|contains|equals|is)\s*([^\s,]+)?", re.I)
UNQUOTED_TEXT_RE = re.compile(
    r"\b(?:contains|mentions|says|shows|lists|has|includes|displays)\s+(?:the\s+(?:word|text|phrase)\s+)?"
    r"(.+?)(?=\s+(?:every|hourly|daily|once|on|at)\b|[.,!?]?$)", re.I)

_UNIT = {"s": "s", "m": "m", "h": "h", "d": "d", "w": "w"}
_OPS = {"=": "eq", "==": "eq", "equals": "eq", "is": "eq", "is not": "ne", "!=": "ne",
        ">": "gt", "above": "gt", "is above": "gt", "over": "gt", "<": "lt", "below": "lt",
        "is below": "lt", "under": "lt", ">=": "gte", "<=": "lte", "contains": "contains",
        "exists": "exists", "missing": "missing", "is missing": "missing",
        "is true": "truthy", "is truthy": "truthy"}

ABSENT_HINTS = ("no longer", "disappear", "is gone", "goes away", "removed", "stops showing",
                "not say", "doesn't say", "does not say", "isn't", "is not")
DOWN_HINTS = ("is down", "goes down", "offline", "unreachable", "not responding")
STATUS_HINTS = ("is up", "goes live", "go live", "is live", "is online", "back online", "comes online",
                "responds", "is reachable", "available", "launches", "opens")
CHANGE_HINTS = ("changes", "changed", "change", "updated", "updates", "is modified", "anything new", "new post")


class ParseError(ValueError):
    pass


def _interval(text: str, default: str) -> tuple[str | None, str | None]:
    if m := CRON_RE.search(text):
        return None, m.group(1)
    if m := EVERY_RE.search(text):
        amount = m.group(1) or "1"
        unit = _UNIT[m.group(2)[0].lower()]
        return format_duration(parse_duration(f"{amount}{unit}")), None
    lowered = text.lower()
    for word, value in (("hourly", "1h"), ("daily", "1d"), ("every hour", "1h"), ("every day", "1d"),
                        ("every minute", "1m"), ("weekly", "1w")):
        if word in lowered:
            return value, None
    return default, None


def _name_for(url: str, check: str, params: dict[str, Any]) -> str:
    host = urlparse(url).netloc.removeprefix("www.") or url
    if check == "keyword":
        verb = "without" if params.get("absent") else "shows"
        return f'{host} {verb} "{params.get("text") or params.get("regex")}"'
    if check == "status":
        return f"{host} is {'down' if params.get('down') else 'live'}"
    if check == "selector":
        return f"{host} has {params['selector']}"
    if check == "json":
        return f"{host} {params.get('path', '$')} {params.get('op', '')} {params.get('value', '')}".strip()
    return f"{host} changes"


def parse_rules(text: str, default_interval: str = "15m") -> dict[str, Any]:
    """Rule-based parser. Returns a watch spec dict; raises ParseError if no URL."""
    urls = URL_RE.findall(text)
    if not urls:
        raise ParseError("I need a URL to watch, e.g. “tell me when https://example.com says \"Book now\"”.")
    url = urls[0].rstrip(".,;:!?)")
    rest = text.replace(urls[0], " ")
    lowered = rest.lower()
    interval, cron = _interval(rest, default_interval)

    params: dict[str, Any] = {}
    selector = BACKTICK_RE.search(rest)
    rest_wo = rest.replace(selector.group(0), " ") if selector else rest
    quoted = QUOTED_RE.search(rest_wo)
    field = FIELD_RE.search(rest_wo)
    unquoted = None
    if not quoted and not selector and (m := UNQUOTED_TEXT_RE.search(rest_wo)):
        phrase = m.group(1).strip(" \"'")
        if phrase and not any(phrase.lower().startswith(h) for h in CHANGE_HINTS + ("been ",)):
            unquoted = phrase

    if field:
        check = "json"
        op = _OPS.get(field.group(2).lower(), "eq")
        params = {"path": field.group(1), "op": op}
        if op not in ("exists", "missing", "truthy") and field.group(3):
            value: Any = field.group(3).strip("\"'")
            try:
                value = float(value) if "." in value else int(value)
            except ValueError:
                pass
            params["value"] = value
    elif quoted or unquoted:
        check = "keyword"
        phrase = quoted.group(1) if quoted else unquoted
        params = {"text": phrase}
        if any(h in lowered for h in ABSENT_HINTS):
            params["absent"] = True
        if selector:
            params["selector"] = selector.group(1)
    elif any(h in lowered for h in CHANGE_HINTS):
        check = "change"
        if selector:
            params["selector"] = selector.group(1)
    elif selector:
        check = "selector"
        params = {"selector": selector.group(1)}
    elif any(h in lowered for h in DOWN_HINTS):
        check, params = "status", {"down": True}
    else:
        # "tell me when https://… goes live / is up / is available" and plain URLs.
        check = "status"

    spec: dict[str, Any] = {"url": url, "check": check, "params": params, "interval": interval, "cron": cron,
                            "notify_once": bool(re.search(r"\bonce\b|\bjust tell me\b", lowered))}
    spec["name"] = _name_for(url, check, params)
    return spec


# -- AI-powered parser (optional, any provider) --------------------------------

LLM_SYSTEM = """You convert a user's plain-English monitoring request into a JSON watch spec for Nudge,
a bot that checks things on a schedule and sends a Telegram alert when a condition becomes true.

Return ONLY a JSON object: {"check": ..., "url": ..., "name": ..., "params": {...},
"interval": "15m" | null, "cron": null | "<5-field cron, UTC>", "notify_once": false}

Check types and their params:
- "bookmyshow": movie bookings opening on BookMyShow. params {"movie": title, "city": city}. url may be "".
- "district": movie bookings opening on District (district.in). params {"movie": title, "city": city,
  optional "cinema": cinema name}. url may be "".
- "amul": Amul shop products in stock. params {"pincode": 6 digits, "product": words in product name
  (e.g. "whey", "chocolate whey", "milkshake"; "" for all), optional "category": "protein" (default)}.
- "booking": any ticket/registration page; alerts when a Book/Buy/Register button is live. params {}.
- "status": url responds HTTP 2xx/3xx. params {"down": true} to alert when it is unreachable instead.
- "keyword": params {"text": phrase, "absent": true to alert when it DISAPPEARS (e.g. "Sold out"), optional "selector"}.
- "selector": params {"selector": CSS selector, optional "text"}.
- "json": JSON APIs. params {"path": "data.items.0.status", "op": exists|missing|truthy|eq|ne|gt|gte|lt|lte|contains,
  "value": comparison value}.
- "change": alert whenever the page changes. params {optional "selector"}.

Rules: prefer bookmyshow/district/amul when the user names those platforms. Use the exact URL if one is given;
never invent URLs for other sites — if no URL is given and no platform fits, return {"error": "<what is missing>"}.
If a required param is missing (pincode, city, movie), return {"error": "<short question to the user>"}.
Cities: use plain names like "bengaluru", "mumbai", "delhi", "hyderabad", "chennai", "pune", "kolkata".
interval like "5m", "15m", "1h" (default "5m" for bookings and stock, "15m" otherwise; never below "1m").
name: short human label, max 60 chars."""

_URL_OPTIONAL = {"bookmyshow", "district", "amul"}
_DEFAULT_URLS = {"bookmyshow": "https://in.bookmyshow.com/", "district": "https://www.district.in/movies/",
                 "amul": "https://shop.amul.com/en/browse/protein"}


def parse_llm(text: str, cfg: "LLMConfig", default_interval: str = "15m") -> dict[str, Any]:
    """Ask the configured model to parse the request. Raises ParseError/LLMError on failure."""
    from nudge.checkers import REGISTRY
    from nudge.llm import complete_json

    data = complete_json(cfg, LLM_SYSTEM, text)
    if data.get("error"):
        raise ParseError(str(data["error"]))
    check = str(data.get("check") or "")
    if check not in REGISTRY:
        raise ParseError(f"The AI suggested an unknown check type {check!r}")
    url = str(data.get("url") or "")
    if not URL_RE.match(url):
        if check not in _URL_OPTIONAL:
            raise ParseError("I need a URL for that. Send the link of the page to watch.")
        url = _DEFAULT_URLS[check]
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    params = {k: v for k, v in params.items() if v not in (None, "")}
    if check == "amul":
        params["pincode"] = str(params.get("pincode", ""))
    interval = data.get("interval")
    try:
        interval = format_duration(max(60.0, parse_duration(interval))) if interval else None
    except ValueError:
        interval = None
    cron = data.get("cron") or None
    return {"url": url, "check": check, "params": params, "name": str(data.get("name") or url)[:80],
            "interval": interval or (None if cron else default_interval), "cron": cron,
            "notify_once": bool(data.get("notify_once")), "confident": True}


_NEEDS_HELP = {
    "pincode": "Which pincode should I check? Add your 6-digit pincode, e.g. “Amul whey protein at 560001”.",
    "city": "Which city? e.g. “… in Bengaluru”.",
    "movie": "Which movie? Put the title in quotes, e.g. “\"Jailer 2\" on District in Chennai”.",
}


def _platform_spec(text: str, default_interval: str) -> dict[str, Any] | None:
    from nudge import platforms

    spec = platforms.detect(text)
    if not spec:
        return None
    explicit_interval, cron = _interval(text, "")
    spec["interval"] = explicit_interval or (None if cron else ("5m" if spec["check"] != "booking" else "10m"))
    spec["cron"] = cron
    spec["notify_once"] = bool(re.search(r"\bonce\b", text, re.I))
    spec["confident"] = not spec.get("needs")
    return spec


def parse(text: str, *, llm: "LLMConfig | None" = None, force_llm: bool = False,
          default_interval: str = "15m") -> Watch:
    """Plain English -> Watch. Rules first (no AI needed); the configured LLM, if any, handles the rest.

    force_llm=True (Telegram /ai) asks the model first.
    """
    from nudge.llm import LLMError

    text = text.strip()
    spec: dict[str, Any] | None = None
    llm_error: str | None = None

    def try_llm() -> dict[str, Any] | None:
        nonlocal llm_error
        if not llm:
            return None
        try:
            return parse_llm(text, llm, default_interval)
        except (LLMError, ParseError, ValueError) as exc:
            llm_error = str(exc)
            log.info("AI parse failed: %s", exc)
            return None

    if force_llm:
        if not llm:
            raise ParseError("No AI provider is configured. Set OPENAI_API_KEY, GEMINI_API_KEY or ANTHROPIC_API_KEY "
                             "(or NUDGE_LLM_BASE_URL for Ollama), or just describe the watch without /ai.")
        spec = try_llm()  # on failure, fall through to the rules, which may still understand it
    if spec is None:
        spec = _platform_spec(text, default_interval)
    if spec is None:
        try:
            spec = parse_rules(text, default_interval)
            lowered = text.lower()
            spec["confident"] = spec["check"] != "status" or any(h in lowered for h in STATUS_HINTS + DOWN_HINTS)
        except ParseError:
            spec = try_llm() if not force_llm else None
            if spec is None:
                raise ParseError(llm_error or "I need a link to watch, or a platform I know (BookMyShow, District, "
                                 "Amul). e.g. “tell me when \"Jailer 2\" bookings open on District in Bengaluru”.")
    if not spec.get("confident") and not force_llm:
        spec = try_llm() or spec
    for need in spec.get("needs") or []:
        if not spec["params"].get(need):
            raise ParseError(_NEEDS_HELP.get(need, f"Missing {need}."))
    if spec["check"] == "keyword" and not spec["params"].get("text"):
        raise ParseError("What text should I look for? Put it in quotes, e.g. \"Book now\".")
    interval = spec.get("interval") or (None if spec.get("cron") else default_interval)
    if interval and parse_duration(interval) < 60:
        interval = "1m"  # be kind to the sites being watched
    return Watch(id=new_id(), name=spec["name"], url=spec["url"], check=spec["check"], params=spec["params"],
                 interval=interval,
                 cron=spec.get("cron"), notify_once=spec.get("notify_once", False), description=text)


def describe(watch: Watch) -> str:
    """One-line human description of what a watch does."""
    p = watch.params
    if watch.check == "keyword":
        what = f'"{p.get("text") or p.get("regex")}" ' + ("disappears" if p.get("absent") else "appears")
        if p.get("selector"):
            what += f" in {p['selector']}"
    elif watch.check == "status":
        what = "site goes down" if p.get("down") else "site responds OK"
    elif watch.check == "selector":
        what = f"{p.get('selector')} shows up"
    elif watch.check == "json":
        what = f"{p.get('path') or '$'} {p.get('op', 'exists')} {p.get('value', '')}".strip()
    elif watch.check == "change":
        what = "content changes" + (f" in {p['selector']}" if p.get("selector") else "")
    elif watch.check in ("district", "bookmyshow"):
        platform = "District" if watch.check == "district" else "BookMyShow"
        if p.get("movie"):
            what = f"\"{p['movie']}\" bookings open on {platform} in {str(p.get('city', '')).title()}"
        else:
            what = f"bookings open on this {platform} page"
        if p.get("cinema"):
            what += f" at {p['cinema']}"
    elif watch.check == "amul":
        what = f"Amul {p.get('product') or p.get('category') or 'protein'} is in stock at {p.get('pincode')}"
    elif watch.check == "booking":
        what = "a Book/Buy button goes live"
    else:
        what = f"{watch.check} {p}"
    return f"when {what} ({watch.schedule}{', once' if watch.notify_once else ''})"
