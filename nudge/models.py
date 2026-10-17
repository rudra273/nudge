"""Watch definitions and duration helpers."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw]?)\s*$", re.I)


def parse_duration(value: str | int | float) -> float:
    """'30s', '15m', '2h', '1d', or bare seconds -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION_RE.match(str(value))
    if not match:
        raise ValueError(f"Invalid duration: {value!r} (use e.g. 30s, 15m, 2h, 1d)")
    amount, unit = match.groups()
    return float(amount) * _UNITS[(unit or "s").lower()]


def format_duration(seconds: float) -> str:
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def new_id() -> str:
    return secrets.token_hex(3)


@dataclass
class Watch:
    id: str
    name: str
    url: str
    check: str = "keyword"
    params: dict[str, Any] = field(default_factory=dict)
    interval: str | None = "15m"
    cron: str | None = None
    enabled: bool = True
    notify_once: bool = False
    description: str | None = None

    @property
    def schedule(self) -> str:
        return f"cron {self.cron}" if self.cron else f"every {self.interval}"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "check": self.check,
        }
        if self.params:
            data["params"] = self.params
        if self.cron:
            data["cron"] = self.cron
        else:
            data["interval"] = self.interval
        data["enabled"] = self.enabled
        if self.notify_once:
            data["notify_once"] = True
        if self.description:
            data["description"] = self.description
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Watch":
        if not data.get("url"):
            raise ValueError(f"Watch {data.get('name') or data.get('id')!r} has no url")
        interval = data.get("interval")
        return cls(
            id=str(data.get("id") or new_id()),
            name=str(data.get("name") or data["url"]),
            url=str(data["url"]),
            check=str(data.get("check", "keyword")),
            params=dict(data.get("params") or {}),
            interval=str(interval) if interval is not None else ("15m" if not data.get("cron") else None),
            cron=data.get("cron"),
            enabled=bool(data.get("enabled", True)),
            notify_once=bool(data.get("notify_once", False)),
            description=data.get("description"),
        )
