import json

import pytest

from nudge import nlp
from nudge.llm import LLMConfig, LLMError, complete_json, config_from_env


class FakeHTTP:
    def __init__(self, payload, status=200):
        self.payload, self.status, self.calls = payload, status, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        payload, status = self.payload, self.status

        class R:
            status_code = status
            text = __import__("json").dumps(payload)

            def json(self):
                return payload
        return R()


SPEC = {"check": "amul", "url": "", "name": "Amul whey", "params": {"pincode": "560001", "product": "whey"},
        "interval": "5m", "cron": None, "notify_once": False}


@pytest.mark.parametrize("env,provider,model", [
    ({}, None, None),
    ({"OPENAI_API_KEY": "k"}, "openai", "gpt-4.1-mini"),
    ({"GEMINI_API_KEY": "k"}, "gemini", "gemini-2.5-flash"),
    ({"GOOGLE_API_KEY": "k", "NUDGE_LLM_MODEL": "gemini-x"}, "gemini", "gemini-x"),
    ({"ANTHROPIC_API_KEY": "k"}, "anthropic", "claude-haiku-5-5"),
    ({"NUDGE_LLM_PROVIDER": "ollama"}, "openai", "llama3.1"),
    ({"NUDGE_LLM_PROVIDER": "openrouter", "NUDGE_LLM_API_KEY": "k", "NUDGE_LLM_BASE_URL": "https://openrouter.ai/api/v1",
      "NUDGE_LLM_MODEL": "meta/llama"}, "openai", "meta/llama"),
    ({"OPENAI_API_KEY": "k", "NUDGE_LLM": "off"}, None, None),
])
def test_config_from_env(env, provider, model):
    cfg = config_from_env(env)
    assert (cfg.provider if cfg else None, cfg.model if cfg else None) == (provider, model)


def test_unknown_provider():
    with pytest.raises(LLMError):
        config_from_env({"NUDGE_LLM_PROVIDER": "skynet", "NUDGE_LLM_API_KEY": "k"})


def test_openai_shape():
    http = FakeHTTP({"choices": [{"message": {"content": json.dumps(SPEC)}}]})
    out = complete_json(LLMConfig("openai", "m", "key"), "sys", "hi", session=http)
    assert out == SPEC
    call = http.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer key" and call["json"]["messages"][0]["role"] == "system"


def test_ollama_base_url_without_key():
    http = FakeHTTP({"choices": [{"message": {"content": "```json\n" + json.dumps(SPEC) + "\n```"}}]})
    out = complete_json(LLMConfig("openai", "llama3.1", "", "http://localhost:11434/v1"), "s", "u", session=http)
    assert out["check"] == "amul" and "Authorization" not in http.calls[0]["headers"]
    assert http.calls[0]["url"] == "http://localhost:11434/v1/chat/completions"


def test_anthropic_shape():
    http = FakeHTTP({"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(SPEC)}],
                     "stop_reason": "end_turn"})
    assert complete_json(LLMConfig("anthropic", "claude-haiku-5-5", "k"), "s", "u", session=http) == SPEC
    assert http.calls[0]["headers"]["x-api-key"] == "k"


def test_gemini_shape():
    http = FakeHTTP({"candidates": [{"content": {"parts": [{"text": json.dumps(SPEC)}]}}]})
    assert complete_json(LLMConfig("gemini", "gemini-2.5-flash", "k"), "s", "u", session=http) == SPEC
    assert http.calls[0]["url"].endswith("/models/gemini-2.5-flash:generateContent")


def test_http_error():
    with pytest.raises(LLMError, match="401"):
        complete_json(LLMConfig("openai", "m", "bad"), "s", "u", session=FakeHTTP({"error": "nope"}, status=401))


def test_parse_uses_llm_only_when_rules_fail(monkeypatch):
    calls = []

    def fake_complete(cfg, system, user, session=None):
        calls.append(user)
        return dict(SPEC)

    monkeypatch.setattr("nudge.llm.complete_json", fake_complete)
    cfg = LLMConfig("openai", "m", "k")
    # rules understand this: no AI call
    assert nlp.parse("amul whey at 560001", llm=cfg).check == "amul" and calls == []
    # rules can't: AI fills in
    w = nlp.parse("my favourite protein drink from that gujarat dairy, bangalore 560001", llm=cfg)
    assert w.check == "amul" and w.params["pincode"] == "560001" and len(calls) == 1
    # /ai forces it
    nlp.parse('https://x.com says "hi"', llm=cfg, force_llm=True)
    assert len(calls) == 2


def test_parse_falls_back_to_rules_when_ai_fails(monkeypatch):
    def boom(*a, **k):
        raise LLMError("down")

    monkeypatch.setattr("nudge.llm.complete_json", boom)
    w = nlp.parse('https://x.com says "hi"', llm=LLMConfig("openai", "m", "k"), force_llm=True)
    assert w.check == "keyword"
    with pytest.raises(nlp.ParseError, match="down"):
        nlp.parse("something vague", llm=LLMConfig("openai", "m", "k"))


def test_ai_question_is_relayed(monkeypatch):
    monkeypatch.setattr("nudge.llm.complete_json", lambda *a, **k: {"error": "Which city are you in?"})
    with pytest.raises(nlp.ParseError, match="Which city"):
        nlp.parse("tickets for the new marvel movie please", llm=LLMConfig("gemini", "m", "k"))
