"""Tiny Telegram Bot API client (sendMessage + getUpdates)."""

from __future__ import annotations

import html
import logging
from typing import Any

import requests

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_ids: list[str], session: requests.Session | None = None):
        self.token = token
        self.chat_ids = [str(c) for c in chat_ids]
        self.session = session or requests.Session()

    def _call(self, method: str, http_timeout: float = 30, **payload: Any) -> Any:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        r = self.session.post(url, json=payload, timeout=http_timeout)
        data = r.json()
        if not data.get("ok"):
            raise TelegramError(f"{method} failed: {data.get('description', r.status_code)}")
        return data["result"]

    def send(self, text: str, chat_id: str | None = None) -> None:
        targets = [chat_id] if chat_id else self.chat_ids
        for target in targets:
            try:
                self._call("sendMessage", chat_id=target, text=text, parse_mode="HTML",
                           disable_web_page_preview=False)
            except (requests.RequestException, TelegramError) as exc:
                log.error("Telegram send to %s failed: %s", target, exc)

    def get_updates(self, offset: int, timeout: int = 0) -> list[dict]:
        return self._call("getUpdates", http_timeout=timeout + 15, offset=offset, timeout=timeout,
                          allowed_updates=["message"])

    def is_allowed(self, chat_id: Any) -> bool:
        return str(chat_id) in self.chat_ids


def esc(text: Any) -> str:
    return html.escape(str(text), quote=False)
