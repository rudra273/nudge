"""Settings, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from nudge.checkers.base import BROWSER_UA
from nudge.llm import LLMConfig, LLMError, config_from_env


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _split_ids(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass
class Settings:
    watches_path: Path = Path("watches.yaml")
    state_path: Path = Path(".nudge-state.json")
    telegram_token: str = ""
    telegram_chat_ids: list[str] = field(default_factory=list)
    telegram_api_base: str = "https://api.telegram.org"
    tick_seconds: int = 30
    default_interval: str = "15m"
    user_agent: str = BROWSER_UA
    timeout: float = 20.0
    llm: LLMConfig | None = None
    llm_error: str | None = None
    browser: bool = False
    plugins: list[str] = field(default_factory=list)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(os.environ.get("NUDGE_ENV_FILE", ".env"))
        env = os.environ
        try:
            llm, llm_error = config_from_env(), None
        except LLMError as exc:
            llm, llm_error = None, str(exc)
        return cls(
            watches_path=Path(env.get("NUDGE_WATCHES", "watches.yaml")),
            state_path=Path(env.get("NUDGE_STATE", ".nudge-state.json")),
            telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_ids=_split_ids(env.get("TELEGRAM_CHAT_ID", "")),
            telegram_api_base=env.get("TELEGRAM_API_BASE", "https://api.telegram.org"),
            tick_seconds=int(env.get("NUDGE_TICK", "30")),
            default_interval=env.get("NUDGE_DEFAULT_INTERVAL", "15m"),
            user_agent=env.get("NUDGE_USER_AGENT", cls.user_agent),
            timeout=float(env.get("NUDGE_TIMEOUT", "20")),
            llm=llm,
            llm_error=llm_error,
            browser=env.get("NUDGE_BROWSER", "").lower() in ("1", "true", "yes", "on"),
            plugins=_split_ids(env.get("NUDGE_PLUGINS", "")),
        )
