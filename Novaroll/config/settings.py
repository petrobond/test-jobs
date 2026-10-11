#!/usr/bin/env python3
"""
Настройки рантайма: всё приходит из окружения/`.env`, секретов в коде нет.

Единая точка входа для приложения, скриптов и eval. Загрузка `.env` — тем же
загрузчиком, что и в `llm/env.py` (переменные окружения приоритетнее файла),
поэтому локально, в контейнере и в Streamlit Cloud поведение одинаковое.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llm.env import ROOT
from llm.env import TIER_CHEAP
from llm.env import TIER_EXPENSIVE
from llm.env import load_env
from llm.providers import ChatProvider
from llm.providers import build_provider
from pipeline.extract import DEFAULT_CONFIDENCE_THRESHOLD

DATA_DIR = ROOT / "data" / "golden_set"
EMAILS_PATH = DATA_DIR / "emails.jsonl"
LABELS_PATH = DATA_DIR / "labels.jsonl"
EVAL_REPORT_PATH = ROOT / "data" / "eval_report.json"

#: прайс по моделям, USD за 1M токенов (input, output).
#: Источники: DeepSeek docs (off-peak) и Artificial Analysis для gpt-6-sol.
PRICES: dict[str, tuple[float, float]] = {
    "deepseek-flash": (0.15, 0.60),  # cache miss, off-peak
    "deepseek-v4-pro": (0.66, 1.98),  # cache miss, off-peak
    "gpt-4o": (2.50, 10.00),
    "gpt-6-sol": (2.00, 10.00),
}


def price_for(model: str) -> tuple[float, float]:
    """(input, output) цена за 1M токенов; (0,0) для неизвестной модели."""
    return PRICES.get(model, (0.0, 0.0))


@dataclass(frozen=True)
class Settings:
    """Настройки прототипа (аналитика и генерация разведены по провайдерам)."""

    env: dict[str, str]
    #: извлечение карточки (аналитика): по умолчанию DeepSeek
    extract_provider: str = "deepseek"
    extract_tier: str = TIER_CHEAP
    #: генерация синтетики: по умолчанию DeepSeek v4-pro, пока OpenAI-ключ не подтверждён
    gen_provider: str = "deepseek"
    gen_tier: str = TIER_EXPENSIVE
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = env if env is not None else load_env()
        try:
            threshold = float(env.get("CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD))
        except ValueError:
            threshold = DEFAULT_CONFIDENCE_THRESHOLD
        return cls(
            env=env,
            extract_provider=(env.get("EXTRACT_PROVIDER") or "deepseek").strip().lower(),
            extract_tier=(env.get("EXTRACT_TIER") or TIER_CHEAP).strip().lower(),
            gen_provider=(env.get("GEN_PROVIDER") or "deepseek").strip().lower(),
            gen_tier=(env.get("GEN_TIER") or TIER_EXPENSIVE).strip().lower(),
            confidence_threshold=threshold,
        )

    def extractor(self) -> ChatProvider:
        """Провайдер для извлечения полей."""
        return build_provider(self.extract_provider, self.extract_tier, self.env)

    def generator(self) -> ChatProvider:
        """Провайдер для генерации синтетических данных."""
        return build_provider(self.gen_provider, self.gen_tier, self.env)

    def price_for(self, model: str) -> tuple[float, float]:
        return price_for(model)

    def as_dict(self) -> dict:
        return {
            "extract": {"provider": self.extract_provider, "tier": self.extract_tier},
            "generate": {"provider": self.gen_provider, "tier": self.gen_tier},
            "confidence_threshold": self.confidence_threshold,
        }


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Читает окружение и собирает настройки (единая точка входа)."""
    return Settings.from_env(env)


__all__ = [
    "DATA_DIR",
    "EMAILS_PATH",
    "EVAL_REPORT_PATH",
    "LABELS_PATH",
    "PRICES",
    "Settings",
    "load_settings",
    "price_for",
]
