"""Scheduling and running checks, deciding when to notify."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from croniter import croniter

from nudge.checkers import CheckContext, CheckResult, get_checker
from nudge.checkers.base import BROWSER_UA, Fetcher, http_fetch
from nudge.models import Watch, parse_duration
from nudge.store import Store
from nudge.telegram import esc

log = logging.getLogger(__name__)

ERROR_ALERT_AFTER = 3  # consecutive failures before sending an error nudge

Notify = Callable[[str], None]


def next_run(watch: Watch, last_checked: float | None) -> float:
    """Epoch seconds when this watch is next due (0 = right now)."""
    if last_checked is None:
        return 0.0
    if watch.cron:
        base = datetime.fromtimestamp(last_checked, tz=timezone.utc)
        return croniter(watch.cron, base).get_next(float)
    return last_checked + parse_duration(watch.interval or "15m")


def is_due(watch: Watch, state: dict, now: float, grace: float = 0.0) -> bool:
    return watch.enabled and next_run(watch, state.get("last_checked")) <= now + grace


@dataclass
class Outcome:
    watch: Watch
    result: CheckResult | None
    error: str | None = None
    notified: bool = False


def format_alert(watch: Watch, result: CheckResult) -> str:
    lines = [f"🔔 <b>{esc(watch.name)}</b>", esc(result.summary), f"🔗 {esc(result.link or watch.url)}"]
    if watch.notify_once:
        lines.append("<i>Watch paused (notify once). /resume " + esc(watch.id) + " to re-arm.</i>")
    return "\n".join(lines)


def format_error(watch: Watch, error: str, count: int) -> str:
    return (f"⚠️ <b>{esc(watch.name)}</b> failed {count} checks in a row\n"
            f"<code>{esc(error[:300])}</code>\n🔗 {esc(watch.url)}")


def run_watch(store: Store, watch: Watch, notify: Notify | None = None, *, now: float | None = None,
              fetcher: Fetcher = http_fetch, timeout: float = 20.0, user_agent: str = BROWSER_UA,
              browser: bool = False) -> Outcome:
    now = time.time() if now is None else now
    state = store.state_for(watch)
    memo = dict(state.get("memo") or {})
    ctx = CheckContext(previous=dict(state), fetcher=fetcher, timeout=timeout, user_agent=user_agent,
                       browser=browser, memo=memo)
    state["last_checked"] = now
    try:
        result = get_checker(watch.check)(watch, ctx)
    except Exception as exc:  # any checker failure counts as an error, never crashes the loop
        error = f"{type(exc).__name__}: {exc}"
        state["errors"] = state.get("errors", 0) + 1
        state["last_error"] = error
        log.warning("[%s] %s failed: %s", watch.id, watch.name, error)
        if notify and state["errors"] == ERROR_ALERT_AFTER:
            notify(format_error(watch, error, state["errors"]))
        return Outcome(watch, None, error=error)

    was_matched = bool(state.get("matched"))
    state.update(matched=result.matched, summary=result.summary, errors=0)
    if memo:
        state["memo"] = memo
    if result.link:
        state["link"] = result.link
    state.pop("last_error", None)
    if result.fingerprint is not None:
        state["fingerprint"] = result.fingerprint

    fire = result.matched and (not result.edge or not was_matched)
    if fire:
        state["last_notified"] = now
        if watch.notify_once:
            store.set_enabled(watch.id, False)
        if notify:
            notify(format_alert(watch, result))
    log.info("[%s] %s: %s%s", watch.id, watch.name, result.summary, " (notified)" if fire else "")
    return Outcome(watch, result, notified=fire)


def run_due(store: Store, notify: Notify | None = None, *, now: float | None = None, grace: float = 0.0,
            force: bool = False, **kwargs) -> list[Outcome]:
    now = time.time() if now is None else now
    outcomes = []
    for watch in list(store.watches):
        if force and not watch.enabled:
            continue
        if force or is_due(watch, store.state_for(watch), now, grace):
            outcomes.append(run_watch(store, watch, notify, now=now, **kwargs))
    return outcomes
