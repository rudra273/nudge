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
from nudge.telegram import Message, button, esc

log = logging.getLogger(__name__)

ERROR_ALERT_AFTER = 3  # consecutive failures before sending an error nudge
REMIND_EVERY = 180  # seconds between reminders of an urgent alert nobody acknowledged
REMIND_MAX = 5

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


def is_urgent(watch: Watch) -> bool:
    """Urgent alerts repeat until acknowledged. Default: District watches for a specific date (FDFS)."""
    return bool(watch.params.get("urgent", watch.check == "district" and bool(watch.params.get("date"))))


def alert_buttons(watch: Watch, link: str | None, urgent: bool = False):
    rows = []
    if link and link.startswith("http"):
        rows.append([button("🎟 Book now" if watch.check in ("district", "bookmyshow", "booking") else "🔗 Open",
                            url=link)])
    actions = [button("✅ Got it", f"ack:{watch.id}")] if urgent else []
    actions += [button("🔁 Check", f"chk:{watch.id}"), button("⏸ Pause", f"pause:{watch.id}")]
    rows.append(actions)
    if watch.check == "district" and watch.params.get("updates", True) is not False:
        rows.append([button("🔕 Stop updates", f"upd:{watch.id}:0")])
    return rows


def format_alert(watch: Watch, result: CheckResult, urgent: bool = False) -> Message:
    link = result.link or watch.url
    lines = [f"🔔 <b>{esc(watch.name)}</b>", esc(result.summary), f"🔗 {esc(link)}"]
    if watch.notify_once:
        lines.append("<i>Watch paused (notify once). /resume " + esc(watch.id) + " to re-arm.</i>")
    if urgent:
        lines.append("<i>I'll keep reminding you until you tap ✅ Got it.</i>")
    return Message("\n".join(lines), alert_buttons(watch, link, urgent))


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
    recovered = state.get("errors", 0) >= ERROR_ALERT_AFTER
    state.update(matched=result.matched, summary=result.summary, errors=0)
    if memo:
        state["memo"] = memo
    if result.link:
        state["link"] = result.link
    state.pop("last_error", None)
    if result.fingerprint is not None:
        state["fingerprint"] = result.fingerprint

    if recovered and notify:
        notify(f"✅ <b>{esc(watch.name)}</b> is working again\n{esc(result.summary)}")

    fire = result.matched and (not result.edge or not was_matched)
    if fire:
        state["last_notified"] = now
        if watch.notify_once:
            store.set_enabled(watch.id, False)
        urgent = is_urgent(watch)
        alert = format_alert(watch, result, urgent)
        if urgent:
            state["unacked"] = {"text": str(alert), "link": result.link or watch.url, "at": now, "count": 0}
        if notify:
            notify(alert)
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
    remind_unacked(store, notify, now=now)
    return outcomes


def remind_unacked(store: Store, notify: Notify | None, *, now: float | None = None) -> int:
    """Re-send urgent alerts until someone taps ✅ Got it (every REMIND_EVERY, at most REMIND_MAX times)."""
    now = time.time() if now is None else now
    sent = 0
    for watch in store.watches:
        state = store.state_for(watch)
        pending = state.get("unacked")
        if not pending or now - pending.get("at", 0) < REMIND_EVERY:
            continue
        pending["count"] = pending.get("count", 0) + 1
        pending["at"] = now
        if notify:
            notify(Message(f"⏰ <b>Reminder {pending['count']}/{REMIND_MAX}</b>\n{pending['text']}",
                           alert_buttons(watch, pending.get("link"), urgent=True)))
            sent += 1
        if pending["count"] >= REMIND_MAX:
            state.pop("unacked", None)
    return sent
