# 🔔 Nudge

**Don't keep checking. Get nudged.**

Tell Nudge what to watch. Nudge tells you when it happens.

Nudge is a small, self-hostable bot that checks websites, APIs and services on a schedule and sends you a
Telegram message when something you care about happens: tickets go on sale, a product is back in stock,
registration opens, a new release ships, a site comes online.

```
you  → tell me when https://tickets.example.com/dune no longer says "Sold out" every 5 min
nudge → 👀 Watching tickets.example.com without "Sold out" (a1b2c3)
        when "Sold out" disappears (every 5m)
...
nudge → 🔔 tickets.example.com without "Sold out"
        "Sold out" is gone from the page
        🔗 https://tickets.example.com/dune
```

- **Plain English.** Describe the watch in a sentence, from Telegram or the CLI. Works offline with a
  rule-based parser; set `ANTHROPIC_API_KEY` and Claude handles fuzzier requests.
- **One nudge per event.** You get an alert when a condition goes from false to true, not on every check.
- **Manage from Telegram.** `/list`, `/pause`, `/resume`, `/remove`, `/check`.
- **Extensible.** Five built-in checkers, and you can add your own in a few lines of Python.
- **Runs anywhere.** GitHub Actions (free, no server), a VPS, Docker, or any box with Python 3.10+.
- **Minimal.** Four small dependencies, two files of state, no database, no dashboard.

## Quick start

```bash
git clone https://github.com/rudra273/nudge && cd nudge
pip install .                 # or: pip install ".[llm]" for Claude-powered parsing
cp .env.example .env          # add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

nudge test                    # sends a test message to Telegram
nudge add 'tell me when https://example.com/tickets says "Book now" every 5 minutes'
nudge list
nudge run                     # scheduler + Telegram bot, Ctrl-C to stop
```

Without Telegram configured, alerts go to stdout, which is handy for trying things out.

### Getting Telegram credentials

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy
   `message.chat.id` into `TELEGRAM_CHAT_ID`. (If Nudge is already running, message it and it will reply
   with your chat id.)

Only chats listed in `TELEGRAM_CHAT_ID` (comma-separated) can control the bot and receive alerts.

## Telling Nudge what to watch

Send the bot a message, or run `nudge add "<request>"`. Add `--dry-run` to see what was understood
without saving it.

| You say | Nudge watches for |
|---|---|
| `tell me when https://site/tickets says "Book now"` | the text appearing (`keyword`) |
| `https://shop/item no longer says "Sold out", every 5 min` | the text disappearing (`keyword`, `absent`) |
| `ping me when https://site/concerts mentions Coldplay` | an unquoted word or phrase (`keyword`) |
| `tell me when https://new-site.example.com goes live` | an HTTP 2xx/3xx response (`status`) |
| `alert me if https://api.example.com is down every 2 min` | the site being unreachable (`status`, `down`) |
| ``tell me when https://shop/item has `button.buy` `` | a CSS selector matching something (`selector`) |
| `https://api/slots field data.available > 0` | a JSON value meeting a condition (`json`) |
| ``watch https://blog `article h2` for changes daily`` | content changing (`change`) |

Things the rule-based parser understands:

- **Schedule:** `every 5 minutes`, `every 2h`, `hourly`, `daily`, `cron "0 9 * * 1-5"` (UTC). The default is `15m`.
- **Once:** add `once` to pause the watch after the first nudge.
- **Text:** put it in quotes. Phrases like `no longer`, `disappears` or `is gone` mean "alert when it's gone".
- **CSS selectors:** put them in `backticks`.
- **JSON:** `field <path> <op> <value>`, where op is one of `is`, `=`, `!=`, `>`, `<`, `>=`, `<=`, `contains`,
  `exists`, `is missing`, `is true`.

With `ANTHROPIC_API_KEY` set (and `pip install ".[llm]"`), requests go to Claude first, so looser wording
like *"let me know when the Coldplay Mumbai tickets page stops saying coming soon"* works too. If the Claude
call fails, Nudge falls back to the rules. Set `NUDGE_LLM=off` to turn this off, or `NUDGE_LLM_MODEL` to pick a
different model. Nudge never sends page contents to the model, only your request.

**Tip for restocks and ticket drops:** watching for the blocking text to *disappear* ("Sold out",
"Coming soon", "Notify me") is usually more reliable than guessing what the page will say once it's live.

## Managing watches

| Telegram | CLI | |
|---|---|---|
| any text, or `/watch <request>` | `nudge add "<request>"` | add a watch |
| `/list` | `nudge list` | show watches, their status and last result |
| `/check <id>` | `nudge check [id…]` | run checks now |
| `/pause <id>` | `nudge pause <id…>` | stop checking |
| `/resume <id>` | `nudge resume <id…>` | start again (and re-arm the alert) |
| `/remove <id>` | `nudge remove <id…>` | delete |

An `<id>` can also be a unique id prefix or the watch's exact name.

You can also edit `watches.yaml` directly; see [`examples/watches.yaml`](examples/watches.yaml). For full control
from the CLI, use flags instead of a sentence:

```bash
nudge add --url https://api.example.com/v1/event --check json \
  --param path=data.registration.open --param op=truthy --every 10m --name "Marathon registration"
```

## Checkers

| `check` | Matches when | `params` |
|---|---|---|
| `status` | the URL returns 2xx/3xx | `down: true` to invert (also matches when unreachable), `status: [200, 204]` |
| `keyword` | the text or regex is in the visible page text | `text` or `regex`, `absent`, `selector`, `case_sensitive` |
| `selector` | a CSS selector matches at least `min_count` elements | `selector`, `min_count` (1), `text` |
| `json` | a value in a JSON response satisfies `op`/`value` | `path` (e.g. `data.items[0].status`), `op`, `value` |
| `change` | the content differs from the last check | `selector` or `path` (JSON) to scope it, `ignore` (regex) |

`op` is one of `exists`, `missing`, `truthy`, `falsy`, `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`,
`len_gt`, `len_gte`, `len_lt`, `len_lte`, `len_eq`.

Every checker also accepts `method`, `headers` and `body` in `params`, for APIs that need a POST or an auth header.

### Notification rules

- Nudge alerts when a watch goes from *not matched* to *matched*. If it stays matched, it stays quiet. If it
  stops matching and later matches again, you get another alert.
- `change` watches alert on every change. The first check only records a baseline.
- `notify_once: true` pauses the watch after the first alert. `/resume` re-arms it.
- After 3 failed checks in a row (timeouts, blocked requests, bad JSON), you get one warning.

### Custom checkers

A checker is a function that takes `(watch, ctx)` and returns a `CheckResult`:

```python
# my_checkers.py
from nudge.checkers import CheckResult, checker

@checker("price_below")
def price_below(watch, ctx):
    price = ctx.fetch(watch).json()["price"]
    return CheckResult(price <= watch.params["max"], f"Price is {price}")
```

Load it with `NUDGE_PLUGINS=my_checkers` (the module has to be importable) and use `check: price_below`. A
fuller example is in [`examples/my_checkers.py`](examples/my_checkers.py). `ctx.fetch(watch)` returns a response
with `.status`, `.text`, `.json()` and a parsed `.soup` (BeautifulSoup). A checker doesn't have to use HTTP at all.

## Deploying

### GitHub Actions (free, no server)

The included workflow [`.github/workflows/nudge.yml`](.github/workflows/nudge.yml) runs every 10 minutes.

1. Fork this repo (make it **private** if your watch URLs are personal).
2. Add repository **secrets** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (and optionally `ANTHROPIC_API_KEY`).
3. Add a repository **variable** `NUDGE_ENABLED` = `true`.
4. Commit a `watches.yaml`, or just message your bot. Each run handles pending Telegram commands, runs any
   checks that are due, and commits `watches.yaml` back if you changed it.

Bot replies arrive on the next run, so expect a delay of up to about 10 minutes, and GitHub can delay scheduled runs
further when it's busy. GitHub also disables scheduled workflows in public repos after 60 days without
activity. Runtime state is kept in the Actions cache.

### VPS (systemd)

```bash
sudo useradd -m nudge && sudo -iu nudge
git clone https://github.com/rudra273/nudge && cd nudge
python3 -m venv .venv && .venv/bin/pip install ".[llm]"
cp .env.example .env && nano .env
```

`/etc/systemd/system/nudge.service`:

```ini
[Unit]
Description=Nudge notification bot
After=network-online.target

[Service]
User=nudge
WorkingDirectory=/home/nudge/nudge
ExecStart=/home/nudge/nudge/.venv/bin/nudge run
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now nudge && journalctl -u nudge -f
```

In this mode the bot replies instantly, because it long-polls Telegram between checks.

### Docker

```bash
cp .env.example .env    # fill it in
docker compose up -d    # watches and state live in ./data
docker compose exec nudge nudge add 'tell me when https://example.com says "Open"'
```

### Plain cron, or any scheduler

If you'd rather not run a daemon, `nudge run --once` does a single pass: it handles bot messages, runs the checks
that are due, and exits. `--grace` counts checks that are due within that window as due, so a slightly early cron
run doesn't skip them.

```cron
*/5 * * * * cd /home/me/nudge && .venv/bin/nudge run --once --grace 1m
```

This works the same way on Cloud Run jobs, Fly.io machines, Kubernetes CronJobs and similar. Just keep
`NUDGE_WATCHES` and `NUDGE_STATE` on persistent storage.

## Configuration

All settings come from environment variables, or from a `.env` file in the working directory.

| Variable | Default | |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | | bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | | allowed chat ids, comma-separated |
| `ANTHROPIC_API_KEY` | | turns on Claude-powered parsing |
| `NUDGE_LLM` | `auto` | `auto` (on when a key is set), `on`, `off` |
| `NUDGE_LLM_MODEL` | `claude-opus-5-5` | model used for parsing |
| `NUDGE_WATCHES` | `watches.yaml` | watch definitions |
| `NUDGE_STATE` | `.nudge-state.json` | runtime state (last results, Telegram offset) |
| `NUDGE_TICK` | `30` | seconds between scheduler ticks in `nudge run` |
| `NUDGE_DEFAULT_INTERVAL` | `15m` | interval for watches that don't set one |
| `NUDGE_TIMEOUT` | `20` | HTTP timeout, in seconds |
| `NUDGE_USER_AGENT` | NudgeBot UA | User-Agent header sent with requests |
| `NUDGE_PLUGINS` | | modules that register custom checkers |

## How it works

```
watches.yaml ──► scheduler (interval / cron) ──► checker (status|keyword|selector|json|change|plugin)
                                                     │
.nudge-state.json ◄── last result, fingerprint ◄─────┤
                                                     ▼
                                        false→true? ──► Telegram 🔔
Telegram commands ──► parser (rules / Claude) ──► watches.yaml
```

The code is small enough to read in one sitting: `nudge/engine.py` (scheduling and alert decisions),
`nudge/checkers/` (checkers), `nudge/nlp.py` (parsing), `nudge/bot.py` (Telegram commands), `nudge/cli.py`.

## Limitations

- Pages that render their content with JavaScript only show their initial HTML. Point Nudge at the site's JSON
  API instead; you can usually find it in your browser's network tab.
- Be polite: use sensible intervals and respect each site's terms. Some sites block automated requests.

## Development

```bash
pip install -e ".[dev,llm]"
pytest
```

MIT licensed.
