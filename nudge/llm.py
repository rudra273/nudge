"""Optional AI parsing, for any provider. Nudge works fully without it.

Providers (plain HTTPS, no SDKs needed):
  openai     — OpenAI, or any OpenAI-compatible API via NUDGE_LLM_BASE_URL
               (Ollama, OpenRouter, Groq, Together, LM Studio, vLLM, Azure-compatible gateways...)
  anthropic  — Claude
  gemini     — Google Gemini

Configure with NUDGE_LLM_PROVIDER / NUDGE_LLM_MODEL / NUDGE_LLM_API_KEY, or just set one of
OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY (GOOGLE_API_KEY) and it is picked up.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-haiku-5-5",
    "gemini": "gemini-2.5-flash",
}
KEY_VARS = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}
ALIASES = {"claude": "anthropic", "gpt": "openai", "chatgpt": "openai", "google": "gemini",
           "ollama": "openai", "openrouter": "openai", "groq": "openai", "compatible": "openai"}
LOCAL_BASE_URLS = {"ollama": "http://localhost:11434/v1"}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: str = ""
    base_url: str | None = None
    timeout: float = 45.0

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def config_from_env(env: dict[str, str] | None = None) -> LLMConfig | None:
    """Build the LLM config from the environment, or None when AI is off / not configured."""
    env = dict(os.environ if env is None else env)
    if env.get("NUDGE_LLM", "auto").lower() in ("0", "off", "false", "no", "none"):
        return None
    raw = env.get("NUDGE_LLM_PROVIDER", "").strip().lower()
    provider = ALIASES.get(raw, raw)
    base_url = env.get("NUDGE_LLM_BASE_URL") or LOCAL_BASE_URLS.get(raw)
    key = env.get("NUDGE_LLM_API_KEY", "")
    if not provider:
        for name, vars_ in KEY_VARS.items():
            if any(env.get(v) for v in vars_):
                provider = name
                break
        else:
            if base_url:
                provider = "openai"
            else:
                return None
    if provider not in DEFAULT_MODELS:
        raise LLMError(f"Unknown NUDGE_LLM_PROVIDER {raw!r}; use openai, anthropic, gemini (or ollama/openrouter)")
    key = key or next((env[v] for v in KEY_VARS[provider] if env.get(v)), "")
    if not key and not base_url:
        return None
    model = env.get("NUDGE_LLM_MODEL") or (env.get("OLLAMA_MODEL", "llama3.1") if raw == "ollama" else
                                            DEFAULT_MODELS[provider])
    return LLMConfig(provider=provider, model=model, api_key=key, base_url=base_url)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMError(f"model did not return JSON: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def complete_json(cfg: LLMConfig, system: str, user: str, session: requests.Session | None = None) -> dict[str, Any]:
    """Ask the model for a single JSON object."""
    http = session or requests
    try:
        if cfg.provider == "openai":
            base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
            headers = {"Content-Type": "application/json"}
            if cfg.api_key:
                headers["Authorization"] = f"Bearer {cfg.api_key}"
            body = {"model": cfg.model, "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            r = http.post(f"{base}/chat/completions", json=body, headers=headers, timeout=cfg.timeout)
            if r.status_code == 400 and "response_format" in r.text:  # some compatible servers lack JSON mode
                body.pop("response_format")
                r = http.post(f"{base}/chat/completions", json=body, headers=headers, timeout=cfg.timeout)
            _raise(r)
            text = r.json()["choices"][0]["message"]["content"] or ""
        elif cfg.provider == "anthropic":
            base = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
            r = http.post(f"{base}/v1/messages", timeout=cfg.timeout,
                          headers={"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": cfg.model, "max_tokens": 4000, "system": system,
                                "messages": [{"role": "user", "content": user}]})
            _raise(r)
            data = r.json()
            if data.get("stop_reason") == "refusal":
                raise LLMError("model declined the request")
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        elif cfg.provider == "gemini":
            base = (cfg.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
            r = http.post(f"{base}/models/{cfg.model}:generateContent", timeout=cfg.timeout,
                          headers={"x-goog-api-key": cfg.api_key, "Content-Type": "application/json"},
                          json={"systemInstruction": {"parts": [{"text": system}]},
                                "contents": [{"role": "user", "parts": [{"text": user}]}],
                                "generationConfig": {"responseMimeType": "application/json", "temperature": 0}})
            _raise(r)
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        else:
            raise LLMError(f"unsupported provider {cfg.provider}")
    except requests.RequestException as exc:
        raise LLMError(f"{cfg.label} request failed: {exc}") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, LLMError):
            raise
        raise LLMError(f"{cfg.label} returned an unexpected response: {exc}") from exc
    try:
        return _extract_json(text)
    except ValueError as exc:
        raise LLMError(f"{cfg.label} returned invalid JSON: {exc}") from exc


def _raise(r: requests.Response) -> None:
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
