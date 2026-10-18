"""Tiny Telegram Bot API client (messages, inline buttons, callbacks, long polling)."""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

MAX_LEN = 4096


class TelegramError(RuntimeError):
    pass


Buttons = list[list[dict[str, str]]]  # rows of inline keyboard buttons


def button(text: str, data: str | None = None, url: str | None = None) -> dict[str, str]:
    """An inline keyboard button: `data` comes back as a callback query, `url` opens a link."""
    return {"text": text, "url": url} if url else {"text": text, "callback_data": (data or text)[:64]}


class Message(str):
    """A reply or alert with inline buttons. Still a plain str, so print() and other notify callbacks work."""

    buttons: Buttons

    def __new__(cls, text: str, buttons: Buttons | None = None) -> "Message":
        obj = super().__new__(cls, text)
        obj.buttons = [row for row in (buttons or []) if row]
        return obj


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def split_text(text: str, limit: int = MAX_LEN - 96) -> list[str]:
    """Split a long message on line breaks so each part fits Telegram's limit."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if current and len(current) + 1 + len(line) > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts


class Telegram:
    def __init__(self, token: str, chat_ids: list[str], session: requests.Session | None = None,
                 api_base: str = "https://api.telegram.org"):
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.chat_ids = [str(c) for c in chat_ids]
        self.session = session or requests.Session()

    def _call(self, method: str, http_timeout: float = 30, **payload: Any) -> Any:
        url = f"{self.api_base}/bot{self.token}/{method}"
        for attempt in range(2):
            r = self.session.post(url, json=payload, timeout=http_timeout)
            data = r.json()
            if data.get("ok"):
                return data["result"]
            retry = (data.get("parameters") or {}).get("retry_after")
            if r.status_code == 429 and retry and attempt == 0:  # flood control: wait as told, once
                time.sleep(min(float(retry), 30))
                continue
            raise TelegramError(f"{method} failed: {data.get('description', r.status_code)}")
        raise TelegramError(f"{method} failed")  # pragma: no cover

    def _send_one(self, target: str, text: str, buttons: Buttons | None) -> int | None:
        payload: dict[str, Any] = {"chat_id": target, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": False}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        try:
            return self._call("sendMessage", **payload).get("message_id")
        except TelegramError as exc:
            if "parse entities" not in str(exc):
                raise
            payload.pop("parse_mode")  # broken HTML from a site's text: send it plain rather than not at all
            payload["text"] = strip_html(text)
            return self._call("sendMessage", **payload).get("message_id")

    def send(self, text: str, chat_id: str | None = None, buttons: Buttons | None = None) -> int | None:
        """Send to one chat (or all allowed chats). Returns the last message id sent, if any."""
        buttons = buttons if buttons is not None else getattr(text, "buttons", None)
        targets = [chat_id] if chat_id else self.chat_ids
        last = None
        for target in targets:
            parts = split_text(str(text))
            try:
                for i, part in enumerate(parts):
                    last = self._send_one(target, part, buttons if i == len(parts) - 1 else None)
            except (requests.RequestException, TelegramError, ValueError) as exc:
                log.error("Telegram send to %s failed: %s", target, exc)
        return last

    def edit(self, chat_id: str, message_id: int, text: str, buttons: Buttons | None = None) -> None:
        """Replace a message's text and buttons (used when a button is tapped)."""
        buttons = buttons if buttons is not None else getattr(text, "buttons", None)
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": split_text(str(text))[0],
                                   "parse_mode": "HTML", "disable_web_page_preview": False,
                                   "reply_markup": {"inline_keyboard": buttons or []}}
        try:
            self._call("editMessageText", **payload)
        except (requests.RequestException, TelegramError, ValueError) as exc:
            if "not modified" not in str(exc):
                log.warning("Telegram edit failed: %s; sending instead", exc)
                self.send(text, chat_id, buttons)

    def edit_buttons(self, chat_id: str, message_id: int, buttons: Buttons | None) -> None:
        """Replace only a message's buttons, keeping its text."""
        try:
            self._call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                       reply_markup={"inline_keyboard": buttons or []})
        except (requests.RequestException, TelegramError, ValueError) as exc:
            log.debug("editMessageReplyMarkup failed: %s", exc)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])
        except (requests.RequestException, TelegramError, ValueError) as exc:
            log.debug("answerCallbackQuery failed (probably too old): %s", exc)

    def get_updates(self, offset: int, timeout: int = 0) -> list[dict]:
        return self._call("getUpdates", http_timeout=timeout + 15, offset=offset, timeout=timeout,
                          allowed_updates=["message", "callback_query"])

    def get_me(self) -> dict:
        return self._call("getMe")

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Register the command menu shown in Telegram's UI."""
        try:
            self._call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])
        except (requests.RequestException, TelegramError) as exc:
            log.warning("setMyCommands failed: %s", exc)

    def is_allowed(self, chat_id: Any) -> bool:
        return str(chat_id) in self.chat_ids


def esc(text: Any) -> str:
    return html.escape(str(text), quote=False)
