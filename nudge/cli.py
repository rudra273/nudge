"""Command-line interface: `nudge add|list|pause|resume|remove|check|run|test`."""

from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import sys
import time
from typing import Any

import yaml

from nudge import __version__, nlp
from nudge.bot import process_updates
from nudge.checkers import REGISTRY, load_plugins
from nudge.config import Settings
from nudge.engine import next_run, run_due, run_watch
from nudge.models import Watch, new_id, parse_duration
from nudge.store import Store
from nudge.telegram import Telegram

log = logging.getLogger("nudge")


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        load_plugins(settings.plugins)
        self.tg = Telegram(settings.telegram_token, settings.telegram_chat_ids) if settings.telegram_enabled else None

    def store(self) -> Store:
        return Store.load(self.settings.watches_path, self.settings.state_path)

    def notify(self, message: str) -> None:
        if self.tg and self.tg.chat_ids:
            self.tg.send(message)
        else:
            print(_strip_html(message), flush=True)

    @property
    def check_kwargs(self) -> dict[str, Any]:
        return {"timeout": self.settings.timeout, "user_agent": self.settings.user_agent}


def _parse_param(raw: str) -> tuple[str, Any]:
    key, sep, value = raw.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"--param expects key=value, got {raw!r}")
    return key.strip(), yaml.safe_load(value)  # "true" -> True, "5" -> 5, text stays text


def cmd_add(app: App, args: argparse.Namespace) -> int:
    if args.url:
        watch = Watch(id=new_id(), name=args.name or args.url, url=args.url, check=args.check,
                      params=dict(_parse_param(p) for p in args.param), interval=args.every or app.settings.default_interval,
                      cron=args.cron, notify_once=args.once)
        if args.cron:
            watch.interval = None
    else:
        text = " ".join(args.request)
        if not text:
            print("Describe what to watch, or use --url/--check/--param.", file=sys.stderr)
            return 2
        try:
            watch = nlp.parse(text, use_llm=app.settings.llm_enabled and not args.no_llm, model=app.settings.llm_model,
                              default_interval=app.settings.default_interval)
        except nlp.ParseError as exc:
            print(exc, file=sys.stderr)
            return 2
        if args.name:
            watch.name = args.name
        if args.every:
            watch.interval, watch.cron = args.every, None
        if args.once:
            watch.notify_once = True
    if watch.check not in REGISTRY:
        print(f"Unknown check {watch.check!r}. Available: {', '.join(sorted(REGISTRY))}", file=sys.stderr)
        return 2
    if watch.interval:
        parse_duration(watch.interval)  # validate early
    print(yaml.safe_dump({"watches": [watch.to_dict()]}, sort_keys=False, allow_unicode=True).rstrip())
    if args.dry_run:
        return 0
    store = app.store()
    store.add(watch)
    store.save()
    print(f"\n👀 Added {watch.id}: {watch.name} — {nlp.describe(watch)}")
    return 0


def cmd_list(app: App, args: argparse.Namespace) -> int:
    store = app.store()
    if args.json:
        print(json.dumps([{**w.to_dict(), "state": store.state_for(w)} for w in store.watches], indent=2))
        return 0
    if not store.watches:
        print("No watches. Add one: nudge add 'tell me when https://example.com says \"Book now\"'")
        return 0
    now = time.time()
    print(f"{'ID':<7} {'STATUS':<8} {'CHECK':<9} {'SCHEDULE':<16} {'NEXT':<8} NAME / LAST RESULT")
    for w in store.watches:
        st = store.state_for(w)
        status = "paused" if not w.enabled else ("MATCHED" if st.get("matched") else ("error" if st.get("last_error") else "watching"))
        nxt = "-" if not w.enabled else max(0, int(next_run(w, st.get("last_checked")) - now))
        nxt_s = nxt if nxt == "-" else ("now" if nxt == 0 else f"{nxt // 60}m{nxt % 60:02d}s")
        print(f"{w.id:<7} {status:<8} {w.check:<9} {w.schedule:<16} {nxt_s:<8} {w.name}")
        last = st.get("last_error") or st.get("summary")
        if last:
            print(f"{'':<7} └ {last[:110]}")
    return 0


def _set_enabled(app: App, args: argparse.Namespace, enabled: bool) -> int:
    store = app.store()
    try:
        for key in args.ids:
            w = store.set_enabled(key, enabled)
            if enabled:
                store.state_for(w)["matched"] = False
            print(f"{'▶️ Resumed' if enabled else '⏸ Paused'} {w.id}: {w.name}")
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 1
    store.save()
    return 0


def cmd_remove(app: App, args: argparse.Namespace) -> int:
    store = app.store()
    try:
        for key in args.ids:
            w = store.remove(key)
            print(f"🗑 Removed {w.id}: {w.name}")
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 1
    store.save()
    return 0


def cmd_check(app: App, args: argparse.Namespace) -> int:
    store = app.store()
    notify = None if args.quiet else app.notify
    if args.ids:
        try:
            outcomes = [run_watch(store, store.find(k), notify, **app.check_kwargs) for k in args.ids]
        except KeyError as exc:
            print(exc.args[0], file=sys.stderr)
            return 1
    else:
        outcomes = run_due(store, notify, force=True, **app.check_kwargs)
    store.save()
    for o in outcomes:
        mark = "⚠️" if o.error else ("✅" if o.result.matched else "⏳")
        print(f"{mark} {o.watch.id} {o.watch.name}: {o.error or o.result.summary}{'  [notified]' if o.notified else ''}")
    return 1 if any(o.error for o in outcomes) else 0


def tick(app: App, grace: float = 0.0, poll_timeout: int = 0) -> None:
    store = app.store()
    if app.tg:
        process_updates(app.tg, store, app.settings, timeout=poll_timeout)
    run_due(store, app.notify, grace=grace, **app.check_kwargs)
    store.save()


def cmd_run(app: App, args: argparse.Namespace) -> int:
    grace = parse_duration(args.grace)
    if args.once:
        tick(app, grace=grace)
        return 0
    stop = False

    def _stop(*_: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("Nudge %s running: %s, tick %ss, telegram %s, llm %s", __version__, app.settings.watches_path,
             app.settings.tick_seconds, "on" if app.tg else "off", "on" if app.settings.llm_enabled else "off")
    while not stop:
        started = time.time()
        try:
            # With Telegram, long-poll for commands during the idle part of the tick: instant replies, low CPU.
            tick(app, grace=grace, poll_timeout=0)
            while app.tg and not stop and time.time() - started < app.settings.tick_seconds - 1:
                store = app.store()
                remaining = int(app.settings.tick_seconds - (time.time() - started))
                process_updates(app.tg, store, app.settings, timeout=max(1, min(remaining, 25)))
                store.save()  # persists the new update offset and any watch changes
        except Exception:  # keep the daemon alive on unexpected errors
            log.exception("tick failed")
        while not stop and time.time() - started < app.settings.tick_seconds:
            time.sleep(0.5)
    log.info("Nudge stopped")
    return 0


def cmd_test(app: App, args: argparse.Namespace) -> int:
    if not app.tg or not app.tg.chat_ids:
        print("Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.", file=sys.stderr)
        return 1
    app.tg.send("🔔 <b>Nudge test</b>\nIf you can read this, notifications work. Don't keep checking. Get nudged.")
    print("Test message sent.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nudge", description="Tell Nudge what to watch. Nudge tells you when it happens.")
    p.add_argument("--version", action="version", version=f"nudge {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="add a watch from plain English or flags")
    a.add_argument("request", nargs="*", help='e.g. tell me when https://x.com says "Book now"')
    a.add_argument("--url")
    a.add_argument("--check", default="keyword", help="status | keyword | selector | json | change | <plugin>")
    a.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="checker parameter (repeatable)")
    a.add_argument("--name")
    a.add_argument("--every", help="interval, e.g. 5m, 1h")
    a.add_argument("--cron", help='cron expression (UTC), e.g. "*/10 9-18 * * *"')
    a.add_argument("--once", action="store_true", help="pause after the first notification")
    a.add_argument("--no-llm", action="store_true", help="use the offline rule-based parser")
    a.add_argument("--dry-run", action="store_true", help="show the parsed watch without saving")
    a.set_defaults(fn=cmd_add)

    ls = sub.add_parser("list", aliases=["ls"], help="list watches")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(fn=cmd_list)

    for name, fn, helptext in (("pause", lambda app, a: _set_enabled(app, a, False), "pause watches"),
                               ("resume", lambda app, a: _set_enabled(app, a, True), "resume watches"),
                               ("remove", cmd_remove, "remove watches")):
        sp = sub.add_parser(name, aliases=["rm"] if name == "remove" else [], help=helptext)
        sp.add_argument("ids", nargs="+", help="watch id, id prefix, or name")
        sp.set_defaults(fn=fn)

    c = sub.add_parser("check", help="run checks now (all enabled, or the given ids)")
    c.add_argument("ids", nargs="*")
    c.add_argument("-q", "--quiet", action="store_true", help="don't send notifications")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("run", help="run the scheduler (and Telegram bot) continuously")
    r.add_argument("--once", action="store_true", help="single tick: handle bot messages, run due checks, exit")
    r.add_argument("--grace", default="0s", help="treat checks due within this window as due (useful for cron/CI)")
    r.set_defaults(fn=cmd_run)

    t = sub.add_parser("test", help="send a test Telegram notification")
    t.set_defaults(fn=cmd_test)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    for noisy in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return args.fn(App(Settings.from_env()), args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
