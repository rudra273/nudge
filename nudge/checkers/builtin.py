"""Built-in checkers: status, keyword, selector, json, change, booking."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import requests

from nudge.checkers import checker
from nudge.checkers.base import CheckContext, CheckResult, Response


def _page_text(resp: Response, selector: str | None = None) -> str:
    if selector:
        nodes = resp.soup.select(selector)
        return " ".join(n.get_text(" ", strip=True) for n in nodes)
    if "html" not in resp.headers.get("Content-Type", resp.headers.get("content-type", "html")).lower():
        return resp.text
    return resp.soup.get_text(" ", strip=True)


def _snippet(text: str, start: int, end: int, pad: int = 60) -> str:
    left, right = max(0, start - pad), min(len(text), end + pad)
    out = re.sub(r"\s+", " ", text[left:right]).strip()
    return ("…" if left else "") + out + ("…" if right < len(text) else "")


@checker("status")
def check_status(watch, ctx: CheckContext) -> CheckResult:
    """Matched when the URL responds OK (or, with down=true, when it doesn't)."""
    p = watch.params
    want_down = bool(p.get("down"))
    expected = p.get("status")
    try:
        resp = ctx.fetch(watch, allow_error=True)
        if expected is not None:
            codes = expected if isinstance(expected, list) else [expected]
            up = resp.status in [int(c) for c in codes]
        else:
            up = resp.ok
        detail = f"HTTP {resp.status}"
    except requests.RequestException as exc:
        up, detail = False, f"unreachable ({type(exc).__name__})"
    if want_down:
        return CheckResult(not up, f"Site is down: {detail}" if not up else f"Site is up: {detail}")
    return CheckResult(up, f"Site is live: {detail}" if up else f"Not live yet: {detail}")


@checker("keyword")
def check_keyword(watch, ctx: CheckContext) -> CheckResult:
    """Matched when text/regex appears on the page (or disappears, with absent=true)."""
    p = watch.params
    text, pattern = p.get("text"), p.get("regex")
    if not text and not pattern:
        raise ValueError("keyword check needs params.text or params.regex")
    flags = 0 if p.get("case_sensitive") else re.I
    regex = re.compile(pattern if pattern else re.escape(str(text)), flags)
    page = _page_text(ctx.fetch(watch), p.get("selector"))
    found = regex.search(page)
    label = text or f"/{pattern}/"
    if p.get("absent"):
        return CheckResult(not found, f'"{label}" is gone from the page' if not found else f'"{label}" still present')
    if found:
        return CheckResult(True, f'Found "{found.group(0)}": {_snippet(page, found.start(), found.end())}')
    return CheckResult(False, f'"{label}" not found')


@checker("selector")
def check_selector(watch, ctx: CheckContext) -> CheckResult:
    """Matched when a CSS selector matches at least min_count elements (optionally containing text)."""
    p = watch.params
    selector = p.get("selector")
    if not selector:
        raise ValueError("selector check needs params.selector")
    nodes = ctx.fetch(watch).soup.select(selector)
    if p.get("text"):
        needle = str(p["text"]).lower()
        nodes = [n for n in nodes if needle in n.get_text(" ", strip=True).lower()]
    count, minimum = len(nodes), int(p.get("min_count", 1))
    if count >= minimum:
        first = nodes[0].get_text(" ", strip=True)[:200] if nodes else ""
        return CheckResult(True, f"{count} element(s) match `{selector}`" + (f": {first}" if first else ""))
    return CheckResult(False, f"{count} element(s) match `{selector}` (need {minimum})")


# -- json --------------------------------------------------------------------

_MISSING = object()


def _dig(data: Any, path: str) -> Any:
    cur = data
    for part in [x for x in re.split(r"\.|\[(\d+)\]", path or "") if x]:
        if isinstance(cur, list) and part.lstrip("-").isdigit():
            idx = int(part)
            cur = cur[idx] if -len(cur) <= idx < len(cur) else _MISSING
        elif isinstance(cur, dict):
            cur = cur.get(part, _MISSING)
        else:
            cur = _MISSING
        if cur is _MISSING:
            return _MISSING
    return cur


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compare(actual: Any, op: str, expected: Any) -> bool:
    op = op.lower()
    if op == "exists":
        return actual is not _MISSING
    if op == "missing":
        return actual is _MISSING
    if actual is _MISSING:
        return False
    if op == "truthy":
        return bool(actual)
    if op == "falsy":
        return not actual
    if op == "contains":
        if isinstance(actual, (list, dict)):
            return expected in actual or str(expected) in [str(x) for x in actual]
        return str(expected).lower() in str(actual).lower()
    if op in ("len_gt", "len_gte", "len_lt", "len_lte", "len_eq"):
        try:
            actual = len(actual)
        except TypeError:
            return False
        op = op[4:]
    a, e = _num(actual), _num(expected)
    if op in ("eq", "=="):
        return a == e if a is not None and e is not None else str(actual).lower() == str(expected).lower()
    if op in ("ne", "!="):
        return not compare(actual, "eq", expected)
    if a is None or e is None:
        return False
    return {"gt": a > e, ">": a > e, "gte": a >= e, ">=": a >= e,
            "lt": a < e, "<": a < e, "lte": a <= e, "<=": a <= e}.get(op, False)


@checker("json")
def check_json(watch, ctx: CheckContext) -> CheckResult:
    """Matched when a value at a dotted path in a JSON response satisfies op/value."""
    p = watch.params
    path, op, expected = p.get("path", ""), p.get("op", "exists"), p.get("value")
    data = ctx.fetch(watch).json()
    actual = _dig(data, path)
    ok = compare(actual, op, expected)
    shown = "missing" if actual is _MISSING else repr(actual)[:200]
    cond = f"{path or '$'} {op}" + (f" {expected!r}" if expected is not None else "")
    return CheckResult(ok, f"{cond} → {shown}" if ok else f"Not yet: {path or '$'} = {shown}",
                       details={"value": None if actual is _MISSING else actual})


@checker("change")
def check_change(watch, ctx: CheckContext) -> CheckResult:
    """Matched whenever the content changes (scoped by `selector`, or by JSON `path`). First run sets a baseline."""
    p = watch.params
    if p.get("path"):
        value = _dig(ctx.fetch(watch).json(), p["path"])
        text = "" if value is _MISSING else json.dumps(value, sort_keys=True, ensure_ascii=False)
    else:
        text = _page_text(ctx.fetch(watch), p.get("selector"))
    if p.get("ignore"):
        text = re.sub(p["ignore"], "", text)
    text = re.sub(r"\s+", " ", text).strip()
    fp = hashlib.sha256(text.encode()).hexdigest()
    previous = ctx.previous.get("fingerprint")
    if previous is None:
        return CheckResult(False, "Baseline saved", fingerprint=fp, edge=False)
    if previous != fp:
        return CheckResult(True, f"Content changed: {text[:200]}", fingerprint=fp, edge=False)
    return CheckResult(False, "No change", fingerprint=fp, edge=False)


# -- booking -----------------------------------------------------------------

OPEN_PHRASES = ("book tickets", "book ticket", "book now", "buy tickets", "buy ticket", "buy now", "get tickets",
                "register now", "select seats", "add to cart", "reserve now", "book seats")
CLOSED_PHRASES = ("sold out", "coming soon", "notify me", "i'm interested", "i\u2019m interested", "registrations closed",
                  "booking closed", "bookings closed", "out of stock", "currently unavailable")
SHOWTIME_RE = re.compile(r"\b(?:[01]?\d):[0-5]\d\s?(?:AM|PM)\b", re.I)


def booking_state(resp: Response, open_phrases=OPEN_PHRASES, closed_phrases=CLOSED_PHRASES) -> tuple[bool, str]:
    """Look at clickable elements (buttons/links) for a booking call-to-action. Returns (open, reason)."""
    labels = []
    for node in resp.soup.select("button, a, [role=button], input[type=submit]"):
        label = (node.get_text(" ", strip=True) or node.get("value") or node.get("aria-label") or "").strip()
        if label and len(label) <= 40:
            labels.append(label)
    lowered = [lbl.lower() for lbl in labels]
    for phrase in open_phrases:
        for raw, low in zip(labels, lowered):
            if low == phrase or low.startswith(phrase + " ") or (len(phrase) > 8 and phrase in low):
                return True, f'"{raw}" button is live'
    for phrase in closed_phrases:
        for raw, low in zip(labels, lowered):
            if phrase in low:
                return False, f'Page shows "{raw}"'
    text = resp.soup.get_text(" ", strip=True).lower()
    for phrase in closed_phrases:
        if phrase in text:
            return False, f'Page says "{phrase}"'
    return False, "No booking button yet"


@checker("booking")
def check_booking(watch, ctx: CheckContext) -> CheckResult:
    """Matched when a ticket/registration page shows a live "Book"/"Buy"/"Register" button.

    Works on most booking pages (BookMyShow, District, Insider, Ticketmaster, college fests...).
    params: open_text / closed_text to override the phrases, showtimes: true to require showtimes.
    """
    p = watch.params
    resp = ctx.fetch(watch)
    if p.get("showtimes"):
        times = SHOWTIME_RE.findall(resp.soup.get_text(" ", strip=True))
        if times:
            return CheckResult(True, f"Showtimes are up: {', '.join(dict.fromkeys(times[:6]))}")
        return CheckResult(False, "No showtimes yet")
    opens = [s.lower() for s in _as_list(p.get("open_text"))] or OPEN_PHRASES
    closes = [s.lower() for s in _as_list(p.get("closed_text"))] or CLOSED_PHRASES
    is_open, reason = booking_state(resp, tuple(opens), tuple(closes))
    return CheckResult(is_open, f"Bookings are open: {reason}" if is_open else reason)


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]
