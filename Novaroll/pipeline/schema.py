#!/usr/bin/env python3
"""
Доменные типы кейса «Письмо -> карточка CRM».

Только данные и валидация: без сети и без промптов.
Схема карточки повторяет поля из ТЗ (компания, контакт, суть запроса, продукт, срочность)
плюс служебные поля для контроля качества (evidence, confidence, needs_review).
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from llm.providers import Completion
from llm.providers import Usage


class Urgency(str, Enum):
    """Срочность запроса (enum — чтобы eval считал точность по классу)."""

    low = "low"
    medium = "medium"
    high = "high"


URGENCIES = tuple(u.value for u in Urgency)

#: поля карточки, которые сверяются с эталоном в eval
EVAL_FIELDS = (
    "company",
    "contact_name",
    "contact_email",
    "contact_phone",
    "product",
    "urgency",
    "requested_deadline",
    "language",
)

#: поле «суть запроса» — свободный текст, оценивается отдельно (покрытие + LLM-судья)
SUMMARY_FIELD = "request_summary"

#: ключевые поля — их отсутствие автоматически поднимает флаг «требует проверки»
KEY_FIELDS = ("company", "contact_name", "contact_email", "contact_phone", "request_summary")


def _blank_to_none(value):
    """Пустые строки -> None (модель любит отдавать `""` вместо `null`)."""
    if isinstance(value, str):
        v = value.strip()
        if v == "" or v.lower() in {"n/a", "нет", "none", "null", "-"}:
            return None
        return v
    return value


class CrmCard(BaseModel):
    """Карточка для занесения в CRM. Полностью повторяет требуемый выход."""

    model_config = ConfigDict(extra="forbid")

    company: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    request_summary: str = Field(default="", description="суть запроса, 1-2 предложения")
    product: str | None = None
    urgency: Urgency = Urgency.medium
    requested_deadline: str | None = None
    language: str = "ru"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: dict[str, str] = Field(default_factory=dict, description="поле -> цитата из письма")

    _norm = field_validator(
        "company",
        "contact_name",
        "contact_email",
        "contact_phone",
        "product",
        "requested_deadline",
        mode="before",
    )(_blank_to_none)

    def to_output(self, *, missing_fields: list[str], needs_review: bool) -> dict:
        """Финальный JSON карточки для UI/CRM (служебные поля вычисляются снаружи)."""
        data = self.model_dump(mode="json")
        data["missing_fields"] = list(missing_fields)
        data["needs_review"] = bool(needs_review)
        return data


#: поля, которые карточка обязана описать (для промпта и проверок)
CARD_SCHEMA_HINT = {
    "company": "название компании-отправителя или null",
    "contact_name": "ФИО контакта или null",
    "contact_email": "email контакта или null",
    "contact_phone": "телефон контакта или null",
    "request_summary": "суть запроса, 1-2 предложения",
    "product": "о каком продукте/услуге речь, или null",
    "urgency": "срочность: low | medium | high",
    "requested_deadline": "желаемый срок (ISO-дата YYYY-MM-DD) или null",
    "language": "язык письма (код, напр. ru/en)",
    "confidence": "уверенность 0..1",
    "evidence": "объект: поле -> короткая цитата из письма, подтверждающая значение",
}


@dataclass(frozen=True)
class ExtractionResult:
    """Результат извлечения карточки из одного письма (единица eval)."""

    card: CrmCard
    output: dict
    missing_fields: list[str] = field(default_factory=list)
    needs_review: bool = False
    json_valid: bool = False
    attempts: int = 1
    provider: str = ""
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    latency_s: float = 0.0
    issues: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.json_valid

    def as_dict(self) -> dict:
        return {
            "card": self.output,
            "meta": {
                "provider": self.provider,
                "model": self.model,
                "json_valid": self.json_valid,
                "attempts": self.attempts,
                "needs_review": self.needs_review,
                "missing_fields": list(self.missing_fields),
                "issues": list(self.issues),
                "usage": {
                    "prompt_tokens": self.usage.prompt_tokens,
                    "completion_tokens": self.usage.completion_tokens,
                    "reasoning_tokens": self.usage.reasoning_tokens,
                    "total_tokens": self.usage.total_tokens,
                },
                "latency_s": round(self.latency_s, 3),
            },
        }


__all__ = [
    "CARD_SCHEMA_HINT",
    "CrmCard",
    "EVAL_FIELDS",
    "ExtractionResult",
    "KEY_FIELDS",
    "SUMMARY_FIELD",
    "URGENCIES",
    "Urgency",
]
