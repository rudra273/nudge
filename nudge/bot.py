"""Telegram command handling: manage watches by chatting with the bot."""

from __future__ import annotations

import logging
from typing import Callable

import requests

from nudge import nlp
from nudge.config import Settings
from nudge.engine import run_watch
from nudge.models import format_duration, parse_duration
from nudge.store import Store
from nudge.telegram import Telegram, TelegramError, esc

log = logging.getLogger(__name__)

HELP = """👋 <b>Nudge</b> — Don't keep checking. Get nudged.

Just tell me what to watch:
🎬 <i>tell me when "Jailer 2" bookings open on District in Bengaluru</i>
🎬 <i>Dune Part Three on BookMyShow in Mumbai</i>
🎬 <i>"Jailer 2" on District at PVR Orion in Bangalore</i>
🥛 <i>Amul whey protein in stock at 560001</i>
🎟 <i>https://www.district.in/events/…</i> (any ticket page: alerts when booking opens)
🌐 <i>https://shop.example.com/item no longer says "Sold out" every 5 min</i>
🔌 <i>https://api.example.com/status field data.open is true</i>

Commands:
/list — your watches
/check &lt;id&gt; — check now
/pause &lt;id&gt; · /resume &lt;id&gt; · /remove &lt;id&gt;
/every &lt;id&gt; &lt;interval&gt; — e.g. /every a1b2 2m
/ai &lt;request&gt; — let the AI figure it out (if configured)
/status — bot health
/help — this message"""


COMMANDS = [
    ("watch", "Add a watch (or just send the text)"),
    ("list", "Show your watches"),
    ("check", "Check a watch now: /check <id>"),
    ("pause", "Pause a watch: /pause <id>"),
    ("resume", "Resume a watch: /resume <id>"),
    ("remove", "Delete a watch: /remove <id>"),
    ("every", "Change interval: /every <id> 5m"),
    ("ai", "Let the AI parse your request"),
    ("status", "Bot health"),
    ("help", "Examples and commands"),
]


def format_list(store: Store) -> str:
    if not store.watches:
        return "No watches yet. Send me something to watch!"
    lines = ["<b>Your watches</b>"]
    for w in store.watches:
        st = store.state_for(w)
        icon = "⏸" if not w.enabled else ("✅" if st.get("matched") else ("⚠️" if st.get("last_error") else "👀"))
        lines.append(f"{icon} <code>{esc(w.id)}</code> {esc(w.name)}\n    {esc(nlp.describe(w))}")
    return "\n".join(lines)


def handle_text(text: str, store: Store, settings: Settings, notify: Callable[[str], None] | None = None) -> str:
    """Handle one incoming message; returns the reply (HTML)."""
    text = text.strip()
    if not text:
        return HELP
    if text.startswith("/"):
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@", 1)[0].lower()
        arg = arg.strip()
    else:
        cmd, arg = "/watch", text

    try:
        if cmd in ("/start", "/help"):
            return HELP
        if cmd == "/list":
            return format_list(store)
        if cmd in ("/watch", "/add", "/ai"):
            if not arg:
                return "Tell me what to watch, e.g. /watch Amul whey protein in stock at 560001"
            watch = store.add(nlp.parse(arg, llm=settings.llm, force_llm=cmd == "/ai",
                                        default_interval=settings.default_interval))
            reply = (f"👀 Watching <b>{esc(watch.name)}</b> (<code>{esc(watch.id)}</code>)\n"
                     f"{esc(nlp.describe(watch))}")
            if notify is not None:  # run it once right away so the user sees the current state
                alerts: list[str] = []
                outcome = run_watch(store, watch, alerts.append, timeout=settings.timeout,
                                    user_agent=settings.user_agent, browser=settings.browser)
                if outcome.error:
                    reply += f"\n⚠️ First check failed: {esc(outcome.error)}"
                elif alerts:
                    reply += "\n\nIt's already happening:\n" + "\n".join(alerts)
                else:
                    reply += f"\nNow: {esc(outcome.result.summary)}"
            return reply
        if cmd == "/status":
            enabled = sum(w.enabled for w in store.watches)
            ai = settings.llm.label if settings.llm else "off (built-in rules only)"
            return (f"🟢 Nudge is running\nWatches: {enabled} active, {len(store.watches) - enabled} paused\n"
                    f"AI: {esc(ai)}\nBrowser mode: {'on' if settings.browser else 'off'}")
        if cmd == "/every":
            key, _, interval = arg.partition(" ")
            if not interval.strip():
                return "Usage: /every &lt;id&gt; &lt;interval&gt;, e.g. /every a1b2 5m"
            seconds = parse_duration(interval.strip())
            if seconds < 60:
                return "Please use 1m or more, to be kind to the sites you watch."
            watch = store.find(key)
            watch.interval, watch.cron = format_duration(seconds), None
            store.dirty = True
            return f"⏱ <b>{esc(watch.name)}</b> now checks every {watch.interval}"
        if cmd in ("/pause", "/resume", "/remove", "/delete", "/check") and not arg:
            return f"Usage: {cmd} &lt;id&gt; (see /list)"
        if cmd == "/pause":
            return f"⏸ Paused <b>{esc(store.set_enabled(arg, False).name)}</b>"
        if cmd == "/resume":
            watch = store.set_enabled(arg, True)
            store.state_for(watch)["matched"] = False  # re-arm so the next match notifies again
            return f"▶️ Resumed <b>{esc(watch.name)}</b>"
        if cmd in ("/remove", "/delete"):
            return f"🗑 Removed <b>{esc(store.remove(arg).name)}</b>"
        if cmd == "/check":
            watch = store.find(arg)
            outcome = run_watch(store, watch, notify, timeout=settings.timeout, user_agent=settings.user_agent,
                                browser=settings.browser)
            if outcome.error:
                return f"⚠️ <b>{esc(watch.name)}</b>: {esc(outcome.error)}"
            mark = "✅" if outcome.result.matched else "⏳"
            return f"{mark} <b>{esc(watch.name)}</b>: {esc(outcome.result.summary)}"
        return f"Unknown command {esc(cmd)}. Try /help."
    except (KeyError, ValueError) as exc:
        return f"🤔 {esc(exc.args[0] if exc.args else exc)}"


def process_updates(tg: Telegram, store: Store, settings: Settings, timeout: int = 0) -> int:
    """Fetch pending messages, handle authorized ones, advance the offset. Returns count handled."""
    try:
        updates = tg.get_updates(store.telegram_offset, timeout=timeout)
    except (requests.RequestException, TelegramError) as exc:
        log.warning("getUpdates failed: %s", exc)
        return 0
    handled = 0
    for update in updates:
        store.telegram_offset = update["update_id"] + 1
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = message.get("text")
        if chat_id is None or not text:
            continue
        if not tg.is_allowed(chat_id):
            log.warning("Ignoring message from unauthorized chat %s", chat_id)
            tg.send(f"Sorry, this Nudge instance is private. Your chat id is <code>{chat_id}</code>.", str(chat_id))
            continue
        reply = handle_text(text, store, settings, notify=lambda msg, c=str(chat_id): tg.send(msg, c))
        tg.send(reply, str(chat_id))
        handled += 1
    return handled
