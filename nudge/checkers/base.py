from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Callable

import requests
from bs4 import BeautifulSoup


@dataclass
class CheckResult:
    matched: bool
    summary: str
    fingerprint: str | None = None
    # edge=True: notify only when matched flips False -> True (default).
    # edge=False: notify on every matched result (e.g. "content changed").
    edge: bool = True
    details: dict[str, Any] = field(default_factory=dict)


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


def http_fetch(url: str, *, method: str = "GET", headers: dict | None = None, body: Any = None,
               timeout: float = 20.0, user_agent: str = "NudgeBot") -> Response:
    hdrs = {"User-Agent": user_agent, "Accept-Language": "en;q=0.9"}
    hdrs.update(headers or {})
    kwargs: dict[str, Any] = {"headers": hdrs, "timeout": timeout}
    if body is not None:
        kwargs["json" if isinstance(body, (dict, list)) else "data"] = body
    r = requests.request(method.upper(), url, **kwargs)
    return Response(status=r.status_code, text=r.text, url=r.url, headers=dict(r.headers))


@dataclass
class CheckContext:
    """What a checker gets besides the watch: a fetcher and its previous state."""

    previous: dict[str, Any]
    fetcher: Fetcher = http_fetch
    timeout: float = 20.0
    user_agent: str = "NudgeBot"
    _cache: dict[str, Response] = field(default_factory=dict)

    def fetch(self, watch) -> Response:
        if watch.url not in self._cache:
            p = watch.params
            self._cache[watch.url] = self.fetcher(
                watch.url,
                method=p.get("method", "GET"),
                headers=p.get("headers"),
                body=p.get("body"),
                timeout=self.timeout,
                user_agent=self.user_agent,
            )
        return self._cache[watch.url]
