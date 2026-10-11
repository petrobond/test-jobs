#!/usr/bin/env python3
"""
Загрузка `.env` и конфигов провайдеров (без сторонних зависимостей).

Стиль загрузки `.env` и формат ключей — как в `octane-ai-sales-agent/classifier/env.py`:
простые `KEY=VALUE`, комментарии через `#`, переменные окружения имеют приоритет над файлом.
Ключи только из окружения/файла, в git не попадают.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: корень проекта (Novaroll/) — на уровень выше пакета `llm/`
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_ENV_PATH = ROOT / ".env"

TIER_CHEAP = "cheap"
TIER_EXPENSIVE = "expensive"
TIERS = (TIER_CHEAP, TIER_EXPENSIVE)

#: провайдер -> переменные окружения (ключ, лёгкая модель, тяжёлая модель, base_url)
_ENV_KEYS = {
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL_LIGHT", "OPENAI_MODEL_HEAVY", "OPENAI_BASE_URL"),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL_LIGHT", "DEEPSEEK_MODEL_HEAVY", "DEEPSEEK_BASE_URL"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL_LIGHT", "GEMINI_MODEL_HEAVY", "GEMINI_BASE_URL"),
}

#: дефолтные base_url, если переменная не задана
_DEFAULT_BASE_URL = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


class ConfigError(RuntimeError):
    """Нет ключа/модели для провайдера."""


@dataclass(frozen=True)
class ProviderConfig:
    """Провайдер + тир -> конкретная модель и адрес."""

    provider: str
    tier: str
    model: str
    base_url: str
    api_key: str

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def as_dict(self) -> dict:
        """Безопасное представление (без ключа) — для логов и UI."""
        return {
            "provider": self.provider,
            "tier": self.tier,
            "model": self.model,
            "base_url": self.base_url,
        }


def load_env(path: Path | str | None = None, *, override: bool = False) -> dict[str, str]:
    """Читает `KEY=VALUE` из файла; при `override=False` не перетирает `os.environ`."""
    p = Path(path) if path else DEFAULT_ENV_PATH
    env: dict[str, str] = dict(os.environ)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if override or not env.get(k):
            env[k] = v
    return env


def provider_config(provider: str, tier: str, env: dict[str, str] | None = None) -> ProviderConfig:
    """Собирает конфиг провайдера для тира `cheap|expensive`."""
    provider = (provider or "").lower()
    if provider not in _ENV_KEYS:
        raise ConfigError(f"неизвестный провайдер: {provider!r} (доступны: {', '.join(_ENV_KEYS)})")
    if tier not in TIERS:
        raise ConfigError(f"неизвестный тир: {tier!r} (cheap|expensive)")
    env = env if env is not None else load_env()
    key_var, light_var, heavy_var, url_var = _ENV_KEYS[provider]
    api_key = env.get(key_var, "")
    if not api_key:
        raise ConfigError(f"нет {key_var} в .env/окружении")
    model = env.get(heavy_var if tier == TIER_EXPENSIVE else light_var, "")
    if not model:
        raise ConfigError(f"нет {heavy_var if tier == TIER_EXPENSIVE else light_var} в .env/окружении")
    base_url = env.get(url_var) or _DEFAULT_BASE_URL[provider]
    return ProviderConfig(provider=provider, tier=tier, model=model, base_url=base_url, api_key=api_key)


def available_providers(env: dict[str, str] | None = None) -> list[str]:
    """Провайдеры, для которых есть ключ."""
    env = env if env is not None else load_env()
    return [p for p, keys in _ENV_KEYS.items() if env.get(keys[0])]


def thinking_disabled(provider: str, env: dict[str, str] | None = None) -> bool:
    """
    Гасить ли thinking-режим (`{provider}_THINKING=off`).

    Для `deepseek-flash` reasoning включён по умолчанию (весь бюджет может уйти в
    `reasoning_content`, `content` придёт пустым). Извлечение полей — короткая задача,
    поэтому thinking можно отключать явным `{"thinking": {"type": "disabled"}}`.
    """
    env = env if env is not None else load_env()
    raw = (env.get(f"{provider.upper()}_THINKING") or "").strip().lower()
    if raw in ("off", "disabled", "none", "false", "0"):
        return True
    if raw in ("on", "enabled", "true", "1"):
        return False
    # дефолт: для DeepSeek извлечение идёт без thinking (быстрее и дешевле)
    return provider == "deepseek"


__all__ = [
    "ConfigError",
    "DEFAULT_ENV_PATH",
    "ROOT",
    "TIER_CHEAP",
    "TIER_EXPENSIVE",
    "TIERS",
    "ProviderConfig",
    "available_providers",
    "load_env",
    "provider_config",
    "thinking_disabled",
]
