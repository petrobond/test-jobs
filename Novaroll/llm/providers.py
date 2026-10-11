#!/usr/bin/env python3
"""
Провайдеры LLM: OpenAI / DeepSeek / Gemini — один OpenAI-совместимый транспорт.

Все три доступны по `POST {base_url}/chat/completions`, поэтому транспорт один —
`requests`, без LLM-SDK (как в `octane-ai-sales-agent/classifier/providers.py`).

Особенности, учтённые из живых замеров:
    * `deepseek-flash` — reasoning-модель: при малом `max_tokens` `content` приходит пустым
      (всё уходит в `reasoning_content`) -> token-бюджет + повтор с удвоенным бюджетом;
      thinking можно гасить явным `{"thinking": {"type": "disabled"}}` (см. `env.thinking_disabled`);
    * retry только на 429 (`Retry-After`) и 5xx, с лимитом попыток;
    * в лог идёт только хэш сырого ответа и sanitized-ошибка (без PII).
"""
from __future__ import annotations

import hashlib
import random
import re
import time
from dataclasses import dataclass

import requests

from .env import ConfigError
from .env import ProviderConfig
from .env import TIER_CHEAP
from .env import TIER_EXPENSIVE
from .env import load_env
from .env import provider_config
from .env import thinking_disabled

DEFAULT_TIMEOUT = 90.0
MAX_ATTEMPTS = 4  # 1 попытка + 3 ретрая
RATE_LIMIT_ATTEMPTS = 8  # 429: больше попыток + уважение Retry-After
BACKOFF_BASE = 1.0
BACKOFF_CAP = 30.0
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
#: бюджет вывода: reasoning-моделям нужен запас, иначе пустой content
MAX_TOKENS = {TIER_CHEAP: 1024, TIER_EXPENSIVE: 2048}
EMPTY_RETRY_FACTOR = 2
EMPTY_RETRIES_MAX = 3  # 1024 -> 2048 -> 4096 (бюджет строго per-call)
MAX_TOKENS_CAP = 4096

_RE_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_RE_PHONE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")


class ProviderError(RuntimeError):
    """Терминальная ошибка вызова (после ретраев)."""


@dataclass(frozen=True)
class Usage:
    """Расход токенов одного вызова (для экономики и eval)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    @property
    def output_tokens(self) -> int:
        """Биллингуемый выход (reasoning-токены тарифицируются как output)."""
        return max(self.completion_tokens, self.reasoning_tokens)

    @classmethod
    def from_api(cls, usage: dict | None) -> "Usage":
        u = usage or {}
        details = u.get("completion_tokens_details") or {}
        return cls(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            reasoning_tokens=int(details.get("reasoning_tokens") or 0),
            total_tokens=int(u.get("total_tokens") or 0),
        )


@dataclass(frozen=True)
class Completion:
    """Сырой ответ провайдера (без разбора JSON)."""

    content: str = ""
    reasoning: str = ""
    usage: Usage = Usage()
    latency_s: float = 0.0
    status: int = 0
    error: str = ""  # sanitized, без PII
    content_sha: str = ""  # хэш сырого ответа (для разбора без PII)

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.error

    @property
    def empty(self) -> bool:
        return not self.content.strip()


def sanitize_error(text: str, limit: int = 240) -> str:
    """Убирает из текста ошибки ключи/почты/телефоны, обрезает."""
    if not text:
        return ""
    s = _RE_KEY.sub("[KEY]", str(text))
    s = _RE_EMAIL.sub("[EMAIL]", s)
    s = _RE_PHONE.sub("[PHONE]", s)
    s = " ".join(s.split())
    return s[:limit]


def content_sha(content: str) -> str:
    """Короткий хэш содержимого ответа (для логов без PII)."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


class ChatProvider:
    """Базовый провайдер: ретраи, бюджет вывода, учёт usage, toggle thinking."""

    #: передавать ли `response_format={"type":"json_object"}`
    json_mode = True
    #: имя поля бюджета вывода (`max_tokens`, fallback -> `max_completion_tokens`)
    max_tokens_field = "max_tokens"

    def __init__(
        self,
        config: ProviderConfig,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
        session: requests.Session | None = None,
        reasoning: str = "",
        thinking_off: bool = False,
        max_tokens: int | None = None,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.session = session or requests.Session()
        self.reasoning = reasoning
        self.thinking_off = thinking_off
        self.max_tokens = max_tokens or MAX_TOKENS.get(config.tier, MAX_TOKENS[TIER_CHEAP])

    @property
    def label(self) -> str:
        return f"{self.config.provider}:{self.config.model}"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _body(self, messages: list[dict], budget: int | None = None) -> dict:
        body: dict = {
            "model": self.config.model,
            "messages": messages,
            self.max_tokens_field: budget or self.max_tokens,
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.reasoning:
            body["reasoning_effort"] = self.reasoning
        if self.thinking_off:
            body["thinking"] = {"type": "disabled"}
        else:
            # temperature поддерживается только в non-thinking режиме
            body["temperature"] = 0
        return body

    def complete(self, messages: list[dict]) -> Completion:
        """
        Один вызов с ретраями. Наружу исключения не бросает.

        Ретраи: 429 (`Retry-After`/backoff, до `RATE_LIMIT_ATTEMPTS`), 5xx (`MAX_ATTEMPTS`),
        сетевые ошибки. Пустой ответ (reasoning съел бюджет) — повтор с удвоенным бюджетом.
        """
        url = self.config.chat_url
        budget = self.max_tokens
        body = self._body(messages, budget)
        started = time.perf_counter()
        last_error = ""
        empty_retries = 0
        max_loop = max(self.max_attempts, RATE_LIMIT_ATTEMPTS)

        for attempt in range(1, max_loop + 1):
            try:
                resp = self.session.post(url, headers=self._headers(), json=body, timeout=self.timeout)
            except requests.RequestException as exc:  # сеть/таймаут
                last_error = f"net: {type(exc).__name__}"
                if attempt >= self.max_attempts:
                    break
                time.sleep(BACKOFF_BASE * attempt)
                continue

            if resp.status_code == 200:
                completion = self._parse_ok(resp, started)
                if completion.content.strip():
                    return completion
                if completion.error:
                    last_error = completion.error
                    if attempt < self.max_attempts:
                        time.sleep(self._retry_delay(resp, attempt))
                        continue
                    return completion
                # пустой content: у reasoning-моделей весь бюджет ушёл в reasoning
                if empty_retries < EMPTY_RETRIES_MAX and budget < MAX_TOKENS_CAP:
                    empty_retries += 1
                    budget = min(budget * EMPTY_RETRY_FACTOR, MAX_TOKENS_CAP)
                    body = self._body(messages, budget)
                    last_error = "retry: empty content (reasoning budget increased)"
                    continue
                return completion

            payload = ""
            try:
                payload = resp.text[:500]
            except Exception:  # noqa: BLE001
                payload = ""

            # `max_tokens` не поддержан моделью -> повтор с `max_completion_tokens`
            if resp.status_code == 400 and self.max_tokens_field == "max_tokens" and "max_completion_tokens" in payload:
                self.max_tokens_field = "max_completion_tokens"
                body = self._body(messages)
                last_error = "retry: max_completion_tokens"
                continue

            last_error = sanitize_error(f"http {resp.status_code}: {payload}")

            if resp.status_code in RETRY_STATUS:
                limit = RATE_LIMIT_ATTEMPTS if resp.status_code == 429 else self.max_attempts
                if attempt < limit:
                    time.sleep(self._retry_delay(resp, attempt))
                    continue
            break

        return Completion(
            latency_s=time.perf_counter() - started,
            status=0 if last_error.startswith("net") else -1,
            error=last_error or "unknown error",
        )

    def _retry_delay(self, resp: requests.Response, attempt: int) -> float:
        """`Retry-After`, иначе экспоненциальный backoff с джиттером."""
        raw = resp.headers.get("Retry-After", "")
        try:
            if raw:
                return min(float(raw), BACKOFF_CAP)
        except ValueError:
            pass
        base = min(BACKOFF_BASE * (attempt**2), BACKOFF_CAP)
        return base + random.uniform(0, 0.5)

    def _parse_ok(self, resp: requests.Response, started: float) -> Completion:
        try:
            payload = resp.json()
        except ValueError:
            return Completion(latency_s=time.perf_counter() - started, status=200, error="invalid json envelope")
        try:
            message = (payload.get("choices") or [{}])[0].get("message", {})
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
        except (AttributeError, IndexError, TypeError):
            content, reasoning = "", ""
        return Completion(
            content=content,
            reasoning=reasoning,
            usage=Usage.from_api(payload.get("usage")),
            latency_s=time.perf_counter() - started,
            status=200,
            content_sha=content_sha(content),
        )


# --------------------------------------------------------------------------- #
# Провайдеры и фабрика
# --------------------------------------------------------------------------- #
class OpenAiProvider(ChatProvider):
    """OpenAI: `json_object` + `max_tokens` (fallback на `max_completion_tokens`)."""


class DeepSeekProvider(ChatProvider):
    """DeepSeek: `json_object`; thinking гасится по умолчанию (см. `thinking_disabled`)."""


class GeminiProvider(ChatProvider):
    """Gemini (OpenAI-совместимый режим): без `response_format`, JSON — из промпта."""

    json_mode = False


_PROVIDER_CLASS = {
    "openai": OpenAiProvider,
    "deepseek": DeepSeekProvider,
    "gemini": GeminiProvider,
}


def build_provider(provider: str, tier: str, env: dict[str, str] | None = None, **kwargs) -> ChatProvider:
    """Один провайдер на один тир; конфиг берётся из `.env`/окружения."""
    env = env if env is not None else load_env()
    cfg = provider_config(provider, tier, env)
    cls = _PROVIDER_CLASS[cfg.provider]
    return cls(
        cfg,
        reasoning=kwargs.pop("reasoning", (env.get(f"{cfg.provider.upper()}_REASONING_EFFORT") or "").strip()),
        thinking_off=kwargs.pop("thinking_off", thinking_disabled(cfg.provider, env)),
        **kwargs,
    )


def build_registry(
    providers: list[str] | None = None,
    tiers: tuple[str, ...] = (TIER_CHEAP, TIER_EXPENSIVE),
    env: dict[str, str] | None = None,
    **kwargs,
) -> dict[tuple[str, str], ChatProvider]:
    """Реестр `(provider, tier) -> ChatProvider` только по провайдерам с ключом."""
    env = env if env is not None else load_env()
    chosen = [p for p in (providers or ("openai", "deepseek", "gemini")) if env.get(f"{p.upper()}_API_KEY")]
    registry: dict[tuple[str, str], ChatProvider] = {}
    for p in chosen:
        for t in tiers:
            try:
                registry[(p, t)] = build_provider(p, t, env, **kwargs)
            except ConfigError:
                continue
    return registry


__all__ = [
    "ChatProvider",
    "Completion",
    "DeepSeekProvider",
    "GeminiProvider",
    "OpenAiProvider",
    "ProviderError",
    "Usage",
    "build_provider",
    "build_registry",
    "content_sha",
    "sanitize_error",
]



