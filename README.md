# 🔔 Nudge

**Don't keep checking. Get nudged.**

Tell Nudge what to watch. Nudge tells you when it happens.

Nudge is a small, self-hostable Telegram bot that watches booking platforms, shops, websites and APIs,
and messages you the moment something you care about goes live: movie bookings open, Amul protein is
back in stock, a concert's "Book tickets" button appears, registration opens.

```
you   → "Jailer 2" on District at PVR Orion in Bangalore
nudge → 👀 Watching Jailer 2 on District (bengaluru)
        when "Jailer 2" bookings open on District in Bengaluru at PVR Orion (every 5m)
        Now: No shows at PVR Orion yet
  …
nudge → 🔔 Jailer 2 on District (bengaluru)
        Book Jailer 2 Tickets in Bengaluru: bookings open at PVR Orion! Shows: 03:30 PM, 06:55 PM, 09:45 PM
        🔗 https://www.district.in/movies/jailer-2-movie-tickets-in-bengaluru-MV187766
```

- **No AI required.** The core works with built-in rules. BookMyShow, District and Amul are supported out of
  the box, and any URL works with simple conditions.
- **Any AI, if you want it.** Add an OpenAI, Gemini or Claude key, or point it at Ollama, OpenRouter, Groq or any
  OpenAI-compatible server, and it handles fuzzier requests.
- **Telegram is the UI.** Add, list, check, pause, resume, remove and re-schedule watches by chatting. There's no
  dashboard to host.
- **One nudge per event.** You get an alert when something becomes true, not on every check.
- **Runs anywhere.** A home PC or Raspberry Pi, a VPS, Docker, or GitHub Actions. Python 3.10+, four small
  dependencies, state kept in two files.

## Built-in platforms (no AI, no URL needed)

| Platform | Say | How it checks |
|---|---|---|
| **District** (district.in) | `"Jailer 2" on District in Bengaluru`<br>`Avengers Doomsday IMAX FDFS on District at any PVR in Bangalore` | Reads the movie's full schedule and alerts when shows open, filtered by day, format, language and cinema (see below) |
| **BookMyShow** | `Dune Part Three on BookMyShow in Mumbai` | Alerts when the movie shows up in the city's now-booking list. With a movie URL, alerts when the Book button or ticket link appears |
| **Amul shop** | `Amul whey protein in stock at 560001`<br>`amul protein at 400001` | Uses the shop's own stock API for your pincode. Alerts for each matching product that comes back in stock |
| **Any ticket page** | paste the link (District events, Insider, college fests…) | Alerts when a "Book tickets", "Buy now" or "Register now" button goes live |

Cities are understood by common names: Bangalore, Bengaluru, Bombay, Gurgaon (counts as Delhi NCR), Madras and so on.

### First day, first show (District)

District movie pages carry the whole schedule: which days are open, every cinema, every show's screen format and
seats left. Nudge reads it every 2 minutes and alerts the moment matching shows open, with a **🎟 Book now** button
that goes straight to seat selection for the earliest show.

| Add to your request | Watches for |
|---|---|
| `FDFS`, `first day`, `release day`, `on 18 Dec`, `on Friday`, `tomorrow` | shows on that day (`date`: an ISO date, a list, or `release`) |
| `IMAX`, `4DX`, `3D`, `Dolby Atmos`, `ScreenX`, `PXL`, `ICE` | that screen format (`format`) |
| `Tamil`, `Telugu`, `Hindi`, `English`… | that language version (`language`) |
| `at PVR Orion`, `at PVR Orion or INOX Garuda`, `at any PVR`, `at "Sathyam Cinemas"` | those cinemas, matched loosely (`cinema`) |
| `urgent` | repeat the alert every 3 minutes (up to 5 times) until you tap **✅ Got it** |

```
you   → Avengers Doomsday IMAX FDFS on District at any PVR in Bangalore
nudge → 👀 Watching Avengers Doomsday [FDFS · IMAX] on District (bengaluru)
        Now: No shows yet (releasing Fri 18 Dec)
  …
nudge → 🔔 Avengers Doomsday [FDFS · IMAX] on District (bengaluru)
        Avengers: Doomsday: bookings open!
        📅 Fri 18 Dec
        • PVR Orion Mall, Dr Rajkumar Road: 06:00 AM IMAX 3D (12/180 left, filling fast), 09:30 AM IMAX 3D
        [🎟 Book now]  [✅ Got it] [🔁 Check] [⏸ Pause]  [🔕 Stop updates]
```

- After the first alert you also hear about **new days and cinemas** as they open (turn it off with
  **🔕 Stop updates**, or `updates: false`).
- Watches for a specific day (like FDFS) are **urgent by default**: the alert repeats until you tap ✅ Got it.
- A title that matches several films ("Jailer" and "Jailer 2") gets a "Which one?" question with buttons.

> **About BookMyShow:** it uses Cloudflare and blocks many datacenter IPs, including cloud servers and GitHub
> Actions. From a home connection it usually works. If it's blocked, Nudge tells you so (it never sends a false
> alert), and browser mode (below) can help. District and Amul worked fine even from a cloud server.

## Quick start

```bash
git clone https://github.com/rudra273/nudge && cd nudge
python3 -m venv .venv && source .venv/bin/activate
pip install .
cp .env.example .env            # paste TELEGRAM_BOT_TOKEN (from @BotFather)
nudge run                       # message your bot: it replies with your chat id
                                # put that in TELEGRAM_CHAT_ID, restart `nudge run`
```

Then just chat with your bot.

## End-to-end testing checklist

1. **Health check:** `nudge doctor --send` checks your bot token, chat id, AI provider (if any), and whether this
   machine can reach Amul, District and BookMyShow. It also sends you a test message.
2. **Start the bot:** `nudge run` (leave it running).
3. **In Telegram, send:**
   - `/start`: shows examples and commands.
   - `Amul whey protein in stock at <your pincode>`: replies with current stock. If something is already in
     stock, you get the alert straight away.
   - `"<a movie that's running now>" on District in <your city>`: should reply "It's already happening" with
     showtimes and a link.
   - `"<an upcoming movie>" on District in <your city>`: should reply "No showtimes yet (Releasing …)".
   - `<movie> on BookMyShow in <city>`: either works, or reports that it's blocked from your network.
   - `/list`, `/check <id>`, `/every <id> 2m`, `/pause <id>`, `/resume <id>`, `/remove <id>`, `/status`.
4. **Wait for a real alert:** for example, keep an Amul product you want watched and leave `nudge run` going.
5. **From the terminal:** `nudge list` shows the same watches, and `nudge check` runs everything once now.

## Telegram commands

| Command | |
|---|---|
| any text, or `/watch <request>` | add a watch, which runs its first check immediately |
| `/list` | your watches; tap one for its card: check now, pause/resume, interval, new-show alerts, remove |
| `/check <id>` | run a check now |
| `/pause <id>` · `/resume <id>` · `/remove <id>` | manage a watch (`/resume` re-arms the alert) |
| `/every <id> <interval>` | change how often it checks, e.g. `/every a1b2 2m` |
| `/city <name>` · `/pincode <pin>` | defaults for requests that don't name one (`Doomsday on District` just works) |
| `/ai <request>` | have the configured AI parse the request |
| `/status` | health: active watches, AI provider, browser mode |
| `/help` | examples |

Alerts and replies come with buttons (**🎟 Book now**, **🔁 Check**, **⏸ Pause**…), so you rarely need to type an id.
If something is missing ("Which city?"), just reply with the answer, or tap one of the suggested cities.
`<id>` can also be a unique id prefix or the watch's exact name. Only chats listed in `TELEGRAM_CHAT_ID` can use the
bot. Anyone else is told the instance is private, and shown their chat id.

The same actions exist on the CLI: `nudge add "<request>"`, `nudge list`, `nudge check [id]`, `nudge pause|resume|remove <id>`.

## Watching any website or API (no AI)

| You say | Nudge watches for |
|---|---|
| `https://site/tickets says "Book now"` | the text appearing (`keyword`) |
| `https://shop/item no longer says "Sold out", every 5 min` | the text disappearing (`keyword`, `absent`) |
| `https://site/concerts mentions Coldplay` | an unquoted word or phrase |
| `https://new-site.example.com goes live` | an HTTP 2xx/3xx response (`status`) |
| `https://api.example.com is down every 2 min` | the site being unreachable (`status`, `down`) |
| ``https://shop/item has `button.buy` `` | a CSS selector matching (`selector`) |
| `https://api/slots field data.available > 0` | a JSON value meeting a condition (`json`) |
| ``https://blog `article h2` changes daily`` | content changing (`change`) |

- **Schedule:** `every 5 minutes`, `every 2h`, `hourly`, `daily`, `cron "0 9 * * 1-5"` (UTC). Platform watches
  default to 5 minutes; other watches default to 15.
- **Once:** add `once` to pause the watch after the first alert.

## AI (optional, any provider)

Nudge never needs AI. If you configure one, it is used only when the built-in rules can't understand a request,
or when you ask for it with `/ai`. It only sees your sentence, never page contents.

| Provider | Set | Default model |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `gpt-4.1-mini` |
| Google Gemini | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `gemini-2.5-flash` |
| Anthropic Claude | `ANTHROPIC_API_KEY` | `claude-haiku-5-5` |
| Ollama (local, free) | `NUDGE_LLM_PROVIDER=ollama`, `NUDGE_LLM_MODEL=llama3.1` | — |
| OpenRouter, Groq, Together, LM Studio, vLLM… | `NUDGE_LLM_BASE_URL=<openai-compatible url>`, `NUDGE_LLM_API_KEY`, `NUDGE_LLM_MODEL` | — |

Override the model with `NUDGE_LLM_MODEL`, pick a provider explicitly with `NUDGE_LLM_PROVIDER`, or turn AI off
with `NUDGE_LLM=off`. All providers are called over plain HTTPS, so no SDKs are needed.

## Browser mode

Some sites only work in a real browser. Enable headless Chromium with:

```bash
pip install ".[browser]" && playwright install chromium
NUDGE_BROWSER=1 nudge run          # or per watch: params: {browser: true}
```

To use a Chrome or Chromium that's already installed instead of Playwright's download, set
`NUDGE_BROWSER_PATH=/usr/bin/chromium`. Browser mode doesn't change your IP address, so a network that's blocked
stays blocked.

## Checkers (for watches.yaml)

| `check` | Matches when | `params` |
|---|---|---|
| `district` | the movie has shows on District | `movie`, `city` (or a District movie URL); filters `date`, `format`, `language`, `cinema`; `updates`, `urgent` |
| `bookmyshow` | the movie is booking on BookMyShow | `movie`, `city` (or a BookMyShow movie/event URL) |
| `amul` | a matching product is in stock (one alert per newly stocked product) | `pincode`, `product`, `category` (`protein`) |
| `booking` | a Book/Buy/Register button is live | `open_text`, `closed_text`, `showtimes: true` |
| `status` | the URL returns 2xx/3xx | `down: true`, `status: [200]` |
| `keyword` | the text or regex is on the page | `text` or `regex`, `absent`, `selector`, `case_sensitive` |
| `selector` | a CSS selector matches | `selector`, `min_count`, `text` |
| `json` | a JSON value satisfies `op`/`value` | `path`, `op`, `value` |
| `change` | the content changes | `selector` or `path`, `ignore` |

Every checker also accepts `method`, `headers`, `body` and `browser`. See [`examples/watches.yaml`](examples/watches.yaml).

**Safety:** if a site returns an error or a bot-protection page, the check fails with a clear error. It never
counts as "Sold out is gone". After 3 failures in a row you get one warning.

### Custom checkers

```python
# my_checkers.py
from nudge.checkers import CheckResult, checker

@checker("price_below")
def price_below(watch, ctx):
    price = ctx.fetch(watch).json()["price"]
    return CheckResult(price <= watch.params["max"], f"Price is {price}")
```

Load it with `NUDGE_PLUGINS=my_checkers`. A fuller example is in [`examples/my_checkers.py`](examples/my_checkers.py).

## Running it 24/7

**Home PC, Raspberry Pi or VPS (recommended; BookMyShow works best from home):**

```ini
# /etc/systemd/system/nudge.service
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

**Docker:**

```bash
cp .env.example .env && docker compose up -d        # watches and state live in ./data
# with browser mode:  docker compose build --build-arg WITH_BROWSER=true
```

**GitHub Actions (free, no server):** see the setup notes at the top of
[`.github/workflows/nudge.yml`](.github/workflows/nudge.yml). Add the secrets, set the repo variable
`NUDGE_ENABLED=true`, and you're done. Each run stays up for 8 minutes (`--listen 8m`), so checks keep their own
schedule (District movies every 2 minutes) and the bot answers within seconds. Trigger it every 10 minutes (GitHub's
own schedule, or an external cron such as cron-job.org calling `workflow_dispatch`) and it's effectively always on.
BookMyShow is usually blocked there.

**Cron or any scheduler:** `nudge run --once --grace 1m` does one pass: it handles bot messages, runs the checks
that are due, and exits. Add `--listen 8m` to keep running (checks and bot replies) for 8 minutes before exiting.

## Configuration

All settings come from environment variables or `.env`. See [`.env.example`](.env.example) for the full list.
The main ones are `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, the AI variables above, `NUDGE_BROWSER`,
`NUDGE_WATCHES`, `NUDGE_STATE`, `NUDGE_TICK`, `NUDGE_DEFAULT_INTERVAL` and `NUDGE_TIMEOUT`.

## Using Nudge from your own app

Telegram is just one front end. The whole chat interface is a single function, so you can plug Nudge into your
own AI app, a Discord bot, or a web endpoint:

```python
from nudge.bot import handle_text
from nudge.config import Settings
from nudge.store import Store

settings = Settings.from_env()
store = Store.load(settings.watches_path, settings.state_path)
reply = handle_text("Amul whey protein in stock at 560001", store, settings, notify=print)
store.save()
```

Run `nudge run` (or `run_due` from `nudge.engine`) for the scheduling. `nudge list --json` gives you
machine-readable state.

## Limitations

- The BookMyShow and District integrations read public pages, so they can break when those sites change.
  `nudge doctor` and the 3-failure warning tell you when that happens.
- Be polite: use sensible intervals (Nudge won't go below 1 minute from Telegram) and respect each site's terms.

## Development

```bash
pip install -e ".[dev]"
pytest
```

MIT licensed.
