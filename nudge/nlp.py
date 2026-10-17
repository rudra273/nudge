"""Turn plain English into a watch.

A rule-based parser works offline with zero setup. If ANTHROPIC_API_KEY is set
(and the `llm` extra is installed), Claude is used first for richer requests,
falling back to the rules on any failure.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from nudge.models import Watch, format_duration, new_id, parse_duration

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


# -- Claude-powered parser ----------------------------------------------------

LLM_SYSTEM = """You convert a user's plain-English monitoring request into a watch spec for Nudge,
a bot that polls a URL on a schedule and sends a Telegram alert when a condition becomes true.

Check types:
- status: alert when the URL responds with HTTP 2xx/3xx ("goes live", "is up"). Set down=true to alert when it is unreachable instead.
- keyword: alert when `text` appears in the page text (case-insensitive). Set absent=true to alert when it DISAPPEARS
  (best for restocks/ticket drops: watch for "Sold out" / "Coming soon" to vanish). Optional `selector` scopes the search.
- selector: alert when CSS `selector` matches at least one element (optionally containing `text`).
- json: for JSON APIs. `path` is a dotted path (e.g. data.items.0.status), `op` one of
  exists, missing, truthy, eq, ne, gt, gte, lt, lte, contains, len_gt; `value` the comparison value as a string.
- change: alert whenever the page (or `selector` region) changes.

Rules: use the exact URL the user gave; if they gave none but named a well-known site, use its most specific
public URL you are confident about. Prefer keyword with absent=true for "back in stock"/"tickets available" when
the user mentions the current blocking text. interval is like 5m, 15m, 1h (default 15m unless asked; never below 1m).
Use cron only if the user asked for specific times. name is a short human label (max 60 chars)."""


def parse_llm(text: str, model: str, default_interval: str = "15m") -> dict[str, Any] | None:
    try:
        import anthropic
        from pydantic import BaseModel
        from typing import Literal, Optional
    except ImportError:
        log.info("anthropic not installed; using rule-based parser (pip install 'nudge-bot[llm]')")
        return None

    class WatchSpec(BaseModel):
        url: str
        name: str
        check: Literal["status", "keyword", "selector", "json", "change"]
        text: Optional[str] = None
        absent: bool = False
        selector: Optional[str] = None
        path: Optional[str] = None
        op: Optional[str] = None
        value: Optional[str] = None
        down: bool = False
        interval: Optional[str] = None
        cron: Optional[str] = None
        notify_once: bool = False

    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.parse(
            model=model,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=LLM_SYSTEM,
            messages=[{"role": "user", "content": text}],
            output_format=WatchSpec,
        )
    except anthropic.APIError as exc:
        log.warning("Claude parse failed (%s); falling back to rules", exc)
        return None
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return None
    s = response.parsed_output
    if not URL_RE.match(s.url):
        return None

    params: dict[str, Any] = {}
    if s.check == "keyword":
        params = {"text": s.text or ""}
        if s.absent:
            params["absent"] = True
    elif s.check == "json":
        params = {"path": s.path or "", "op": s.op or "exists"}
        if s.value is not None:
            params["value"] = s.value
    elif s.check == "status" and s.down:
        params = {"down": True}
    if s.selector and s.check in ("keyword", "selector", "change"):
        params["selector"] = s.selector
    if s.check == "selector" and s.text:
        params["text"] = s.text
    interval = s.interval
    try:
        if interval:
            interval = format_duration(max(60.0, parse_duration(interval)))
    except ValueError:
        interval = None
    return {"url": s.url, "name": s.name[:80], "check": s.check, "params": params,
            "interval": interval or (None if s.cron else default_interval), "cron": s.cron,
            "notify_once": s.notify_once}


def parse(text: str, *, use_llm: bool = False, model: str = "claude-opus-5-5",
          default_interval: str = "15m") -> Watch:
    spec = parse_llm(text, model, default_interval) if use_llm else None
    if spec is None:
        spec = parse_rules(text, default_interval)
    if spec["check"] == "keyword" and not spec["params"].get("text"):
        raise ParseError("What text should I look for? Put it in quotes, e.g. \"Book now\".")
    return Watch(id=new_id(), name=spec["name"], url=spec["url"], check=spec["check"], params=spec["params"],
                 interval=spec.get("interval"), cron=spec.get("cron"), notify_once=spec.get("notify_once", False),
                 description=text.strip())


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
    else:
        what = f"{watch.check} {p}"
    return f"when {what} ({watch.schedule}{', once' if watch.notify_once else ''})"
