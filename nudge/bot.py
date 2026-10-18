"""Telegram command handling: manage watches by chatting with the bot."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

import requests

from nudge import engine, nlp
from nudge.config import Settings
from nudge.engine import next_run, run_watch
from nudge.models import Watch, format_duration, parse_duration
from nudge.store import Store
from nudge.telegram import Message, Telegram, TelegramError, button, esc

log = logging.getLogger(__name__)

HELP = """👋 <b>Nudge</b> — Don't keep checking. Get nudged.

Just tell me what to watch:
🎬 <i>tell me when "Jailer 2" bookings open on District in Bengaluru</i>
🎬 <i>Dune Part Three on BookMyShow in Mumbai</i>
🎬 <i>"Jailer 2" on District at PVR Orion in Bangalore</i>
🍿 <i>Avengers Doomsday IMAX FDFS on District at any PVR in Bengaluru</i>
🍿 <i>Kantara Tamil on District in Chennai on 24 Dec, urgent</i>
🥛 <i>Amul whey protein in stock at 560001</i>
🎟 <i>https://www.district.in/events/…</i> (any ticket page: alerts when booking opens)
🌐 <i>https://shop.example.com/item no longer says "Sold out" every 5 min</i>
🔌 <i>https://api.example.com/status field data.open is true</i>

Commands:
/list — your watches (tap one to manage it)
/check &lt;id&gt; — check now
/pause &lt;id&gt; · /resume &lt;id&gt; · /remove &lt;id&gt;
/every &lt;id&gt; &lt;interval&gt; — e.g. /every a1b2 2m
/city &lt;name&gt; · /pincode &lt;pin&gt; — defaults for requests without one
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
    ("city", "Default city: /city Bengaluru"),
    ("pincode", "Default pincode: /pincode 560001"),
    ("ai", "Let the AI parse your request"),
    ("status", "Bot health"),
    ("help", "Examples and commands"),
]


INTERVAL_CHOICES = ("1m", "2m", "5m", "15m")


def _icon(watch: Watch, st: dict) -> str:
    return "⏸" if not watch.enabled else ("✅" if st.get("matched") else ("⚠️" if st.get("last_error") else "👀"))


def _ago(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "never"
    secs = max(0, int((now or time.time()) - ts))
    if secs < 60:
        return "just now"
    return f"{format_duration(secs // 60 * 60 if secs < 3600 else secs // 3600 * 3600)} ago"


def format_list(store: Store) -> Message:
    if not store.watches:
        return Message("No watches yet. Send me something to watch!")
    lines = ["<b>Your watches</b>"]
    rows = []
    for w in store.watches:
        st = store.state_for(w)
        lines.append(f"{_icon(w, st)} <code>{esc(w.id)}</code> {esc(w.name)}\n    {esc(nlp.describe(w))}")
        rows.append([button(f"{_icon(w, st)} {w.name[:40]}", f"card:{w.id}")])
    return Message("\n".join(lines), rows[:60])


def watch_card(store: Store, watch: Watch) -> Message:
    """One watch, with buttons for everything you can do to it."""
    st = store.state_for(watch)
    lines = [f"{_icon(watch, st)} <b>{esc(watch.name)}</b> (<code>{esc(watch.id)}</code>)", esc(nlp.describe(watch))]
    last = st.get("last_error") or st.get("summary")
    if last:
        lines.append(f"\nLast check ({_ago(st.get('last_checked'))}):\n{esc(last[:1500])}")
    if not watch.enabled:
        lines.append("\n<i>Paused</i>")
    else:
        due = max(0, int(next_run(watch, st.get("last_checked")) - time.time()))
        lines.append(f"\nNext check: {'now' if due < 60 else 'in ' + format_duration(due // 60 * 60)}")
    wid = watch.id
    toggle = button("▶️ Resume", f"resume:{wid}:c") if not watch.enabled else button("⏸ Pause", f"pause:{wid}:c")
    rows = [[button("🔁 Check now", f"chk:{wid}:c"), toggle],
            [button(("• " if watch.interval == i and not watch.cron else "") + f"⏱ {i}", f"every:{wid}:{i}:c")
             for i in INTERVAL_CHOICES]]
    if watch.check == "district":
        on = watch.params.get("updates", True) is not False
        rows.append([button("🔔 New shows: on" if on else "🔕 New shows: off", f"upd:{wid}:{0 if on else 1}:c")])
    link = st.get("link") or watch.url
    rows.append(([button("🔗 Open", url=link)] if link.startswith("http") else []) +
                [button("🗑 Remove", f"rm:{wid}"), button("⬅️ All watches", "list")])
    return Message("\n".join(lines), rows)


def added_buttons(watch: Watch):
    return [[button("🔁 Check now", f"chk:{watch.id}"), button("⚙️ Manage", f"card:{watch.id}")],
            [button("⏱ 1m", f"every:{watch.id}:1m"), button("⏱ 5m", f"every:{watch.id}:5m"),
             button("🗑 Undo", f"rmy:{watch.id}")]]


# -- actions shared by commands and buttons ------------------------------------------

def set_interval(store: Store, key: str, interval: str) -> Watch:
    seconds = parse_duration(interval.strip())
    if seconds < 60:
        raise ValueError("Please use 1m or more, to be kind to the sites you watch.")
    watch = store.find(key)
    watch.interval, watch.cron = format_duration(seconds), None
    store.dirty = True
    return watch


def pause(store: Store, key: str) -> Watch:
    watch = store.set_enabled(key, False)
    store.state_for(watch).pop("unacked", None)  # pausing counts as "got it"
    return watch


def resume(store: Store, key: str) -> Watch:
    watch = store.set_enabled(key, True)
    store.state_for(watch)["matched"] = False  # re-arm so the next match notifies again
    return watch


def check_now(store: Store, watch: Watch, settings: Settings, notify: Callable[[str], None] | None):
    return run_watch(store, watch, notify, timeout=settings.timeout, user_agent=settings.user_agent,
                     browser=settings.browser)


def check_reply(watch: Watch, outcome) -> str:
    if outcome.error:
        return f"⚠️ <b>{esc(watch.name)}</b>: {esc(outcome.error)}"
    mark = "✅" if outcome.result.matched else "⏳"
    return f"{mark} <b>{esc(watch.name)}</b>: {esc(outcome.result.summary)}"


# -- adding watches: drafts, saved defaults, "which movie?" ------------------------------

DRAFT_TTL = 3600
QUICK_CITIES = ("bengaluru", "mumbai", "delhi", "hyderabad", "chennai", "pune")
_CITY_LABELS = {"delhi": "Delhi NCR"}
_FRESH_REQUEST = re.compile(r"https?://|\b(?:district|bookmyshow|book\s*my\s*show|bms|amul)\b", re.I)


def prefs(store: Store) -> dict:
    return store.state.setdefault("prefs", {})


def _drafts(store: Store) -> dict:
    return store.state.setdefault("drafts", {})


def _draft(store: Store, chat_id: str | None) -> dict | None:
    draft = _drafts(store).get(str(chat_id)) if chat_id else None
    if draft and time.time() - draft.get("at", 0) > DRAFT_TTL:
        _drafts(store).pop(str(chat_id), None)
        return None
    return draft


def _city_buttons(store: Store):
    saved = prefs(store).get("city")
    cities = ([saved] if saved else []) + [c for c in QUICK_CITIES if c != saved]
    labels = [button(_CITY_LABELS.get(c, c.title()), f"city:{c}") for c in cities[:6]]
    return [labels[:3], labels[3:6], [button("✖️ Cancel", "draft:cancel")]]


def add_watch(store: Store, settings: Settings, watch: Watch, notify: Callable[[str], None] | None) -> Message:
    """Save a parsed watch and (when live) run its first check, so the reply shows where things stand."""
    store.add(watch)
    reply = f"👀 Watching <b>{esc(watch.name)}</b> (<code>{esc(watch.id)}</code>)\n{esc(nlp.describe(watch))}"
    rows = added_buttons(watch)
    if notify is not None:
        alerts: list[str] = []
        outcome = check_now(store, watch, settings, alerts.append)
        if outcome.error:
            reply += f"\n⚠️ First check failed: {esc(outcome.error)}"
        elif alerts:
            reply += "\n\nIt's already happening:\n" + "\n".join(alerts)
            rows = getattr(alerts[0], "buttons", [])[:1] + rows  # the Book button first
        else:
            reply += f"\nNow: {esc(outcome.result.summary)}"
    return Message(reply, rows)


def _movie_choices(watch: Watch, settings: Settings) -> list[str]:
    """District pages matching the title, when it is ambiguous ("Jailer" vs "Jailer 2")."""
    from nudge.checkers import CheckContext
    from nudge.platforms import district
    from nudge.platforms.common import city_slug

    if watch.check != "district" or "-MV" in watch.url or not watch.params.get("movie"):
        return []
    ctx = CheckContext(previous={}, timeout=settings.timeout, user_agent=settings.user_agent, browser=settings.browser)
    try:
        return district.find_movies(ctx, watch.params["movie"], city_slug(watch.params["city"], "district"))[:6]
    except Exception as exc:  # the check itself will retry; this is only a nicety
        log.info("movie lookup failed: %s", exc)
        return []


def start_add(text: str, store: Store, settings: Settings, notify: Callable[[str], None] | None,
              chat_id: str | None = None, force_llm: bool = False) -> Message:
    """Parse a request and add it, or ask for what's missing (the next message or button answers it)."""
    from nudge.platforms.district import _title_from_url

    try:
        watch = nlp.parse(text, llm=settings.llm, force_llm=force_llm, default_interval=settings.default_interval,
                          defaults=prefs(store))
    except nlp.MissingInfo as exc:
        if not chat_id:
            raise
        _drafts(store)[str(chat_id)] = {"text": text, "need": exc.need, "at": time.time()}
        rows = _city_buttons(store) if exc.need == "city" else [[button("✖️ Cancel", "draft:cancel")]]
        return Message(f"🤔 {esc(exc)}\n<i>Just reply with the answer.</i>", rows)
    choices = _movie_choices(watch, settings) if notify is not None and chat_id else []
    if len(choices) > 1:
        _drafts(store)[str(chat_id)] = {"text": text, "need": "pick", "at": time.time(),
                                        "watch": watch.to_dict(), "options": choices}
        rows = [[button(_title_from_url(u), f"pick:{i}")] for i, u in enumerate(choices)]
        return Message(f"Which <b>{esc(watch.params['movie'])}</b>?", rows + [[button("✖️ Cancel", "draft:cancel")]])
    _drafts(store).pop(str(chat_id), None)
    return add_watch(store, settings, watch, notify)


def answer_draft(answer: str, draft: dict, store: Store, settings: Settings,
                 notify: Callable[[str], None] | None, chat_id: str) -> Message:
    need, text = draft["need"], draft["text"]
    saved = ""
    if need == "city":
        from nudge.platforms.common import canonical_city

        city = canonical_city(answer)
        if not prefs(store).get("city"):
            prefs(store)["city"] = city
            saved = "\n<i>Saved as your default city (change it with /city).</i>"
        text = f"{text} in {answer}"
    elif need == "pincode":
        pin = re.search(r"\b\d{6}\b", answer)
        if not pin:
            return Message("That doesn't look like a 6-digit pincode. Try again, e.g. 560001.",
                           [[button("✖️ Cancel", "draft:cancel")]])
        prefs(store).setdefault("pincode", pin.group(0))
        text = f"{text} at {pin.group(0)}"
    elif need == "movie":
        text = f'"{answer.strip(chr(34))}" {text}'
    reply = start_add(text, store, settings, notify, chat_id)
    return Message(str(reply) + saved, reply.buttons) if saved else reply


def handle_text(text: str, store: Store, settings: Settings, notify: Callable[[str], None] | None = None,
                chat_id: str | None = None) -> str:
    """Handle one incoming message; returns the reply (HTML, possibly a Message with buttons)."""
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
            draft = _draft(store, chat_id)
            if draft and not text.startswith("/") and not _FRESH_REQUEST.search(arg) and draft["need"] != "pick":
                return answer_draft(arg, draft, store, settings, notify, str(chat_id))
            return start_add(arg, store, settings, notify, chat_id, force_llm=cmd == "/ai")
        if cmd in ("/city", "/pincode"):
            key = cmd[1:]
            if not arg:
                current = prefs(store).get(key)
                return (f"Your default {key} is <b>{esc(current)}</b>. Change it with {cmd} &lt;{key}&gt;."
                        if current else f"No default {key} yet. Set one: {cmd} " +
                        ("Bengaluru" if key == "city" else "560001"))
            if key == "pincode" and not re.fullmatch(r"\d{6}", arg):
                return "A pincode has 6 digits, e.g. /pincode 560001"
            if key == "city":
                from nudge.platforms.common import canonical_city

                arg = canonical_city(arg)
            prefs(store)[key] = arg
            return f"📍 Default {key} set to <b>{esc(arg)}</b>. Requests without one will use it."
        if cmd == "/status":
            enabled = sum(w.enabled for w in store.watches)
            ai = settings.llm.label if settings.llm else "off (built-in rules only)"
            return (f"🟢 Nudge is running\nWatches: {enabled} active, {len(store.watches) - enabled} paused\n"
                    f"AI: {esc(ai)}\nBrowser mode: {'on' if settings.browser else 'off'}")
        if cmd == "/every":
            key, _, interval = arg.partition(" ")
            if not interval.strip():
                return "Usage: /every &lt;id&gt; &lt;interval&gt;, e.g. /every a1b2 5m"
            watch = set_interval(store, key, interval)
            return f"⏱ <b>{esc(watch.name)}</b> now checks every {watch.interval}"
        if cmd in ("/pause", "/resume", "/remove", "/delete", "/check") and not arg:
            return f"Usage: {cmd} &lt;id&gt; (see /list)"
        if cmd == "/pause":
            return f"⏸ Paused <b>{esc(pause(store, arg).name)}</b>"
        if cmd == "/resume":
            return f"▶️ Resumed <b>{esc(resume(store, arg).name)}</b>"
        if cmd in ("/remove", "/delete"):
            return f"🗑 Removed <b>{esc(store.remove(arg).name)}</b>"
        if cmd == "/check":
            watch = store.find(arg)
            return Message(check_reply(watch, check_now(store, watch, settings, notify)),
                           [[button("⚙️ Manage", f"card:{watch.id}")]])
        return f"Unknown command {esc(cmd)}. Try /help."
    except (KeyError, ValueError) as exc:
        return f"🤔 {esc(exc.args[0] if exc.args else exc)}"


@dataclass
class CallbackReply:
    toast: str = ""                  # small popup on the user's screen
    message: str | None = None       # new text (with buttons) for the tapped message, or a new message
    edit: bool = True                # True: replace the tapped message; False: send `message` as a new one
    buttons: list | None = None      # replace only the tapped message's buttons


def handle_callback(data: str, store: Store, settings: Settings,
                    notify: Callable[[str], None] | None = None, chat_id: str | None = None) -> CallbackReply:
    """Handle a tapped inline button. data is "<action>:<watch id>[:<arg>][:c]" (c = tapped on a watch card)."""
    parts = data.split(":")
    action, wid = parts[0], parts[1] if len(parts) > 1 else ""
    on_card = parts[-1] == "c" and len(parts) > 2
    args = parts[2:-1] if on_card else parts[2:]
    try:
        if action == "list":
            return CallbackReply(message=format_list(store))
        if action in ("city", "pick", "draft"):
            draft = _draft(store, chat_id)
            if action == "draft" or not draft:
                _drafts(store).pop(str(chat_id), None)
                return CallbackReply("Cancelled" if action == "draft" else "That question has expired",
                                     Message("✖️ Cancelled") if action == "draft" else None)
            if action == "city":
                return CallbackReply(message=answer_draft(wid, draft, store, settings, notify, str(chat_id)))
            from nudge.platforms.district import _title_from_url

            watch = Watch.from_dict({**draft["watch"], "url": draft["options"][int(wid)]})
            title = _title_from_url(watch.url)
            watch.name = watch.name.replace(watch.params.get("movie") or title, title, 1)
            watch.params["movie"] = title
            _drafts(store).pop(str(chat_id), None)
            return CallbackReply("👀 Watching", add_watch(store, settings, watch, notify))
        watch = store.find(wid)
        if action == "card":
            return CallbackReply(message=watch_card(store, watch))
        if action == "chk":
            outcome = check_now(store, watch, settings, notify)
            if on_card:
                return CallbackReply("Checked", watch_card(store, watch))
            return CallbackReply("Checked", Message(check_reply(watch, outcome),
                                                    [[button("⚙️ Manage", f"card:{watch.id}")]]), edit=False)
        if action == "pause":
            pause(store, wid)
            return CallbackReply("⏸ Paused", watch_card(store, watch) if on_card else None)
        if action == "resume":
            resume(store, wid)
            return CallbackReply("▶️ Resumed", watch_card(store, watch) if on_card else None)
        if action == "every" and args:
            set_interval(store, wid, args[0])
            return CallbackReply(f"⏱ Every {watch.interval}", watch_card(store, watch) if on_card else None)
        if action == "upd" and args:
            watch.params["updates"] = args[0] == "1"
            store.dirty = True
            toast = "🔔 I'll tell you about new shows" if args[0] == "1" else "🔕 No more new-show alerts"
            if on_card:
                return CallbackReply(toast, watch_card(store, watch))
            return CallbackReply(toast, buttons=engine.alert_buttons(watch, store.state_for(watch).get("link")))
        if action == "ack":
            store.state_for(watch).pop("unacked", None)
            return CallbackReply("👍 Got it, no more reminders",
                                 buttons=engine.alert_buttons(watch, store.state_for(watch).get("link")))
        if action == "rm":
            return CallbackReply(message=Message(f"Remove <b>{esc(watch.name)}</b>?", [[
                button("🗑 Yes, remove", f"rmy:{watch.id}"), button("Cancel", f"card:{watch.id}")]]))
        if action == "rmy":
            store.remove(wid)
            return CallbackReply("🗑 Removed", Message(f"🗑 Removed <b>{esc(watch.name)}</b>",
                                                     [[button("⬅️ All watches", "list")]]))
        return CallbackReply("That button is no longer valid")
    except (KeyError, ValueError, IndexError) as exc:
        return CallbackReply(str(exc.args[0] if exc.args else exc)[:180])


def process_updates(tg: Telegram, store: Store, settings: Settings, timeout: int = 0) -> int:
    """Fetch pending messages and button taps, handle authorized ones, advance the offset. Returns count handled."""
    try:
        updates = tg.get_updates(store.telegram_offset, timeout=timeout)
    except (requests.RequestException, TelegramError) as exc:
        log.warning("getUpdates failed: %s", exc)
        return 0
    handled = 0
    for update in updates:
        store.telegram_offset = update["update_id"] + 1
        if update.get("callback_query"):
            handled += _process_callback(tg, store, settings, update["callback_query"])
            continue
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = message.get("text")
        if chat_id is None or not text:
            continue
        if not tg.is_allowed(chat_id):
            log.warning("Ignoring message from unauthorized chat %s", chat_id)
            tg.send(f"Sorry, this Nudge instance is private. Your chat id is <code>{chat_id}</code>.", str(chat_id))
            continue
        reply = handle_text(text, store, settings, notify=lambda msg, c=str(chat_id): tg.send(msg, c),
                            chat_id=str(chat_id))
        tg.send(reply, str(chat_id))
        handled += 1
    return handled


def _process_callback(tg: Telegram, store: Store, settings: Settings, cq: dict) -> int:
    message = cq.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if not chat_id or not tg.is_allowed(chat_id):
        tg.answer_callback(cq.get("id", ""), "This Nudge instance is private.")
        return 0
    reply = handle_callback(cq.get("data") or "", store, settings, notify=lambda msg: tg.send(msg, chat_id),
                            chat_id=chat_id)
    tg.answer_callback(cq.get("id", ""), reply.toast)
    message_id = message.get("message_id")
    if reply.message is not None:
        if reply.edit and message_id:
            tg.edit(chat_id, message_id, reply.message)
        else:
            tg.send(reply.message, chat_id)
    elif reply.buttons is not None and message_id:
        tg.edit_buttons(chat_id, message_id, reply.buttons)
    return 1
