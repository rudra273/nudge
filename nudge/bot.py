"""Telegram command handling: manage watches by chatting with the bot."""

from __future__ import annotations

import logging
from typing import Callable

import requests

from nudge import nlp
from nudge.config import Settings
from nudge.engine import run_watch
from nudge.store import Store
from nudge.telegram import Telegram, TelegramError, esc

log = logging.getLogger(__name__)

HELP = """👋 <b>Nudge</b> — Don't keep checking. Get nudged.

Just tell me what to watch, e.g.
• <i>tell me when https://example.com/tickets no longer says "Sold out"</i>
• <i>https://shop.example.com/item says "Add to cart" every 5 min</i>
• <i>notify me when https://api.example.com/status field data.open is true</i>
• <i>watch https://example.com/blog for changes hourly</i>

Commands:
/watch &lt;request&gt; — add a watch (or just send the text)
/list — show watches
/check &lt;id&gt; — run a check now
/pause &lt;id&gt; · /resume &lt;id&gt; · /remove &lt;id&gt;
/help — this message"""


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
        if cmd in ("/watch", "/add"):
            if not arg:
                return "Tell me what to watch, e.g. /watch https://example.com says \"Book now\""
            watch = store.add(nlp.parse(arg, use_llm=settings.llm_enabled, model=settings.llm_model,
                                        default_interval=settings.default_interval))
            return (f"👀 Watching <b>{esc(watch.name)}</b> (<code>{esc(watch.id)}</code>)\n"
                    f"{esc(nlp.describe(watch))}\n🔗 {esc(watch.url)}")
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
            outcome = run_watch(store, watch, notify, timeout=settings.timeout, user_agent=settings.user_agent)
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
