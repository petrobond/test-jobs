"""Тесты транспорта LLM (сеть мокается — реальных вызовов нет)."""
from __future__ import annotations

from llm.env import ProviderConfig
from llm.providers import DeepSeekProvider
from llm.providers import build_provider
from llm.providers import sanitize_error

CFG = ProviderConfig(
    provider="deepseek", tier="cheap", model="deepseek-flash", base_url="http://fake", api_key="sk-test12345678"
)


class FakeResponse:
    def __init__(self, status_code, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, url, headers=None, json=None, timeout=None):  # noqa: A002, ARG002
        self.calls += 1
        return self._responses.pop(0)


def _ok_payload(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_body_uses_json_mode_and_thinking_off():
    provider = DeepSeekProvider(CFG, thinking_off=True)
    body = provider._body([{"role": "user", "content": "x"}])
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert "temperature" not in body


def test_retry_on_429_then_success():
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "0"}), FakeResponse(200, _ok_payload('{"a":1}'))])
    provider = DeepSeekProvider(CFG, session=session, max_attempts=4)
    completion = provider.complete([{"role": "user", "content": "x"}])
    assert completion.ok and completion.content == '{"a":1}'
    assert session.calls == 2


def test_fallback_to_max_completion_tokens():
    session = FakeSession(
        [
            FakeResponse(400, text="unknown parameter max_completion_tokens"),
            FakeResponse(200, _ok_payload('{"a":2}')),
        ]
    )
    provider = DeepSeekProvider(CFG, session=session, max_attempts=4)
    completion = provider.complete([{"role": "user", "content": "x"}])
    assert completion.ok
    assert provider.max_tokens_field == "max_completion_tokens"


def test_sanitize_error_hides_secrets():
    out = sanitize_error("failed key sk-abcdef123456 for a@b.com tel +7 999 123-45-67")
    assert "sk-abcdef123456" not in out
    assert "a@b.com" not in out


def test_build_provider_reads_env():
    env = {
        "DEEPSEEK_API_KEY": "sk-x12345678",
        "DEEPSEEK_MODEL_LIGHT": "deepseek-flash",
        "DEEPSEEK_MODEL_HEAVY": "deepseek-v4-pro",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    }
    provider = build_provider("deepseek", "expensive", env)
    assert provider.config.model == "deepseek-v4-pro"
    assert provider.thinking_off is True


def test_drops_temperature_on_400():
    """gpt-6-sol принимает только default temperature -> провайдер откатывает параметр."""
    session = FakeSession(
        [
            FakeResponse(400, text="'temperature' does not support 0 with this model"),
            FakeResponse(200, _ok_payload('{"a":3}')),
        ]
    )
    provider = DeepSeekProvider(CFG, session=session, max_attempts=4)
    completion = provider.complete([{"role": "user", "content": "x"}])
    assert completion.ok
    assert provider.temperature is None


def test_temperature_none_not_sent():
    provider = DeepSeekProvider(CFG, temperature=None)
    body = provider._body([{"role": "user", "content": "x"}])
    assert "temperature" not in body
