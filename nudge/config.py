"""Settings, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


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
    tick_seconds: int = 30
    default_interval: str = "15m"
    user_agent: str = "Mozilla/5.0 (compatible; NudgeBot/0.1; +https://github.com/rudra273/nudge)"
    timeout: float = 20.0
    llm_model: str = "claude-opus-5-5"
    llm_enabled: bool = False
    plugins: list[str] = field(default_factory=list)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(os.environ.get("NUDGE_ENV_FILE", ".env"))
        env = os.environ
        llm_flag = env.get("NUDGE_LLM", "auto").lower()
        has_key = bool(env.get("ANTHROPIC_API_KEY"))
        return cls(
            watches_path=Path(env.get("NUDGE_WATCHES", "watches.yaml")),
            state_path=Path(env.get("NUDGE_STATE", ".nudge-state.json")),
            telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_ids=_split_ids(env.get("TELEGRAM_CHAT_ID", "")),
            tick_seconds=int(env.get("NUDGE_TICK", "30")),
            default_interval=env.get("NUDGE_DEFAULT_INTERVAL", "15m"),
            user_agent=env.get("NUDGE_USER_AGENT", cls.user_agent),
            timeout=float(env.get("NUDGE_TIMEOUT", "20")),
            llm_model=env.get("NUDGE_LLM_MODEL", "claude-opus-5-5"),
            llm_enabled=llm_flag in ("1", "true", "yes", "on") or (llm_flag == "auto" and has_key),
            plugins=_split_ids(env.get("NUDGE_PLUGINS", "")),
        )
