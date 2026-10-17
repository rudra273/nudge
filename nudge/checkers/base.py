from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Callable

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/129.0.0.0 Safari/537.36")


class FetchError(RuntimeError):
    """The page could not be fetched in a usable state (HTTP error, timeout...)."""


class BlockedError(FetchError):
    """The site served a bot wall (e.g. Cloudflare) instead of the real page."""


@dataclass
class CheckResult:
    matched: bool
    summary: str
    fingerprint: str | None = None
    # edge=True: notify only when matched flips False -> True (default).
    # edge=False: notify on every matched result (e.g. "content changed", "new item in stock").
    edge: bool = True
    details: dict[str, Any] = field(default_factory=dict)
    # Overrides the link in the alert (e.g. the exact product or booking page).
    link: str | None = None


@dataclass
class Response:
    status: int
    text: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    def json(self) -> Any:
        return json.loads(self.text)

    @cached_property
    def soup(self) -> BeautifulSoup:
        soup = BeautifulSoup(self.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        return soup


Fetcher = Callable[..., Response]

_WALL_MARKERS = ("attention required! | cloudflare", "just a moment...", "cf-chl", "sorry, you have been blocked",
                 "access denied", "request unsuccessful. incapsula", "captcha")


def detect_block(resp: Response) -> str | None:
    """Return a reason if the response looks like a bot wall rather than the real page."""
    if resp.status not in (401, 403, 429, 503):
        return None
    head = resp.text[:20000].lower()
    server = str(resp.headers.get("server", resp.headers.get("Server", ""))).lower()
    if any(m in head for m in _WALL_MARKERS) or "cloudflare" in server:
        who = "Cloudflare" if "cloudflare" in server or "cloudflare" in head else "the site's bot protection"
        return f"blocked by {who} (HTTP {resp.status})"
    return None


def http_fetch(url: str, *, method: str = "GET", headers: dict | None = None, body: Any = None,
               timeout: float = 20.0, user_agent: str = BROWSER_UA, browser: bool = False) -> Response:
    if browser:
        return browser_fetch(url, timeout=timeout, user_agent=user_agent)
    hdrs = {"User-Agent": user_agent, "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
    hdrs.update(headers or {})
    kwargs: dict[str, Any] = {"headers": hdrs, "timeout": timeout}
    if body is not None:
        kwargs["json" if isinstance(body, (dict, list)) else "data"] = body
    r = requests.request(method.upper(), url, **kwargs)
    return Response(status=r.status_code, text=r.text, url=r.url, headers=dict(r.headers))


def browser_fetch(url: str, *, timeout: float = 30.0, user_agent: str = BROWSER_UA) -> Response:
    """Render the page in headless Chromium (pip install 'nudge-bot[browser]' && playwright install chromium)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise FetchError("browser mode needs Playwright: pip install 'nudge-bot[browser]' && "
                         "playwright install chromium") from None
    with sync_playwright() as pw:
        # NUDGE_BROWSER_PATH lets you use an existing Chrome/Chromium instead of Playwright's download.
        b = pw.chromium.launch(executable_path=os.environ.get("NUDGE_BROWSER_PATH") or None)
        try:
            page = b.new_page(user_agent=user_agent, locale="en-IN")
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout, 15) * 1000)
            except Exception:  # busy pages never go idle; what has rendered so far is enough
                pass
            return Response(status=resp.status if resp else 200, text=page.content(), url=page.url,
                            headers=dict(resp.headers) if resp else {})
        finally:
            b.close()


@dataclass
class CheckContext:
    """What a checker gets besides the watch: fetch helpers, its previous state and a persistent memo."""

    previous: dict[str, Any]
    fetcher: Fetcher = http_fetch
    timeout: float = 20.0
    user_agent: str = BROWSER_UA
    browser: bool = False
    memo: dict[str, Any] = field(default_factory=dict)  # saved between checks (e.g. a resolved URL)
    session_factory: Callable[[], requests.Session] | None = None
    _cache: dict[str, Response] = field(default_factory=dict)

    def get(self, url: str, *, allow_error: bool = False, browser: bool | None = None, method: str = "GET",
            headers: dict | None = None, body: Any = None) -> Response:
        """Fetch a URL. Raises BlockedError on bot walls and FetchError on HTTP errors unless allow_error."""
        key = f"{method} {url}"
        use_browser = self.browser if browser is None else bool(browser)
        if key not in self._cache:
            self._cache[key] = self.fetcher(url, method=method, headers=headers, body=body, timeout=self.timeout,
                                            user_agent=self.user_agent, browser=use_browser)
        resp = self._cache[key]
        reason = detect_block(resp)
        if reason:
            hint = ("this network's IP is blocked; run Nudge from a home connection" if use_browser else
                    "try running Nudge from a home connection, or enable browser mode (NUDGE_BROWSER=1)")
            raise BlockedError(f"{reason}: {hint}")
        if not allow_error and resp.status >= 400:
            raise FetchError(f"HTTP {resp.status} from {url}")
        return resp

    def fetch(self, watch, *, allow_error: bool = False) -> Response:
        p = watch.params
        return self.get(watch.url, allow_error=allow_error, browser=p.get("browser"),
                        method=p.get("method", "GET"), headers=p.get("headers"), body=p.get("body"))

    def session(self) -> requests.Session:
        if self.session_factory:
            return self.session_factory()
        s = requests.Session()
        s.headers.update({"User-Agent": self.user_agent, "Accept-Language": "en-IN,en;q=0.9"})
        return s
