#!/usr/bin/env python3
"""
Оркестрация извлечения карточки из письма: LLM -> валидация -> правила -> evidence-check.

Слои защиты от ошибок (важно для CRM, где цена ошибки — неверная карточка):
    1. JSON-режим + robust-parse (снимаем markdown-ограждения, ищем первый объект);
    2. Pydantic-валидация схемы; при провале — один repair-вызов;
    3. rules-фолбэк: e-mail/телефон/язык добираем регулярками, если модель пропустила;
    4. evidence-check: значение, которого нет в письме, — подозрение на галлюцинацию;
    5. needs_review: итоговый флаг «человек, проверь» (низкая уверенность/пропуски/подозрения).
"""
from __future__ import annotations

import json
import re
import time

from llm.providers import ChatProvider
from llm.providers import Completion
from llm.prompts import build_extraction_messages
from llm.prompts import build_repair_messages
from pipeline.rules import contains_evidence
from pipeline.rules import find_emails
from pipeline.rules import find_phones
from pipeline.rules import find_phones_raw
from pipeline.rules import guess_language
from pipeline.rules import phones_equal
from pipeline.rules import urgency_from_rules
from pipeline.schema import KEY_FIELDS
from pipeline.schema import CrmCard
from pipeline.schema import ExtractionResult

#: ниже этой уверенности карточку отдаём на ручную проверку
DEFAULT_CONFIDENCE_THRESHOLD = 0.75
#: поля, к которым применяем evidence-check
#: (дата исключена: модель нормализует её в ISO, дословной цитаты в письме не будет)
EVIDENCE_FIELDS = ("company", "contact_name", "contact_email", "contact_phone", "product")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def parse_json_object(raw: str) -> dict | None:
    """Достаёт JSON-объект из ответа модели (снимает ```-ограждения, ищет первую {...})."""
    if not raw:
        return None
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _coerce_card(payload: dict) -> tuple[CrmCard | None, str]:
    """Собирает CrmCard из сырого словаря; возвращает (card, error)."""
    if not isinstance(payload, dict):
        return None, "payload is not an object"
    known = set(CrmCard.model_fields.keys())
    clean = {k: v for k, v in payload.items() if k in known}
    try:
        card = CrmCard(**clean)
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError
        return None, str(exc)[:300]
    return card, ""


def _apply_rules(card: CrmCard, text: str, issues: list[str]) -> None:
    """Дешёвый фолбэк: добираем очевидные значения и сверяем срочность (in-place)."""
    if not card.contact_email:
        emails = find_emails(text)
        if emails:
            card.contact_email = emails[0]
            issues.append("rule_fallback:contact_email")
    if not card.contact_phone:
        phones = find_phones_raw(text)
        if phones:
            card.contact_phone = phones[0]
            issues.append("rule_fallback:contact_phone")
    if not card.language:
        card.language = guess_language(text)
    rule_urgency = urgency_from_rules(text)
    if rule_urgency and rule_urgency != card.urgency.value:
        issues.append(f"urgency_rule:{card.urgency.value}->{rule_urgency}")
        card.urgency = type(card.urgency)(rule_urgency)


def _evidence_check(card: CrmCard, text: str, issues: list[str]) -> None:
    """Помечаем значения, которых нет в письме (подозрение на галлюцинацию)."""
    for fname in EVIDENCE_FIELDS:
        value = getattr(card, fname)
        if not value:
            continue
        if fname == "contact_phone":
            ok = any(phones_equal(value, p) for p in (find_phones(text) or [value]))
        else:
            ok = contains_evidence(text, value)
        if not ok:
            issues.append(f"unsupported_value:{fname}")


def _missing_key_fields(card: CrmCard) -> list[str]:
    """Ключевые поля, которых не хватает (по схеме карточки)."""
    return [f for f in KEY_FIELDS if not getattr(card, f, None)]


def _should_review(card: CrmCard, issues: list[str], threshold: float, had_error: bool) -> bool:
    """
    Бизнес-правило флага «человек, проверь» (а не «все поля обязательны»).

    Тревога поднимается, только если карточка реально рискованная:
    сбой вызова, значение без подтверждения в письме, низкая уверенность,
    пустая суть запроса или нечем ответить (нет ни e-mail, ни телефона).
    Отсутствие компании/имени само по себе — не повод для тревоги.
    """
    if had_error:
        return True
    if any(i.startswith("unsupported_value:") for i in issues):
        return True
    if card.confidence < threshold:
        return True
    if not card.request_summary.strip():
        return True
    if not (card.contact_email or card.contact_phone):
        return True
    return False


def _repair(email_text: str, bad_output: str, error: str, provider: ChatProvider) -> Completion:
    """Один повтор с просьбой исправить JSON."""
    return provider.complete(build_repair_messages(email_text, bad_output, error))


def extract_card(
    email_text: str,
    provider: ChatProvider,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    max_repairs: int = 1,
) -> ExtractionResult:
    """
    Полный проход по одному письму: вызов LLM, валидация, правила, оценка риска.

    Никогда не бросает исключение: сбой вызова -> `json_valid=False`, флаг needs_review=True
    (fail-open: письмо не теряем, отдаём человеку).
    """
    messages = build_extraction_messages(email_text)
    started = time.perf_counter()
    completion = provider.complete(messages)
    attempts = 1
    issues: list[str] = []
    card: CrmCard | None = None
    raw = completion.content

    if completion.error or completion.empty:
        issues.append(f"llm_error:{completion.error or 'empty content'}")
    else:
        payload = parse_json_object(completion.content)
        if payload is None:
            issues.append("parse_error:no_json_object")
            err = "ответ не является JSON-объектом"
        else:
            card, err = _coerce_card(payload)
        if card is None and max_repairs > 0:
            issues.append(f"schema_error:{err}")
            completion = _repair(email_text, completion.content, err, provider)
            attempts += 1
            payload2 = parse_json_object(completion.content)
            if payload2 is not None:
                card, err2 = _coerce_card(payload2)
                if card is None:
                    issues.append(f"schema_error_repair:{err2}")
            else:
                issues.append("parse_error_repair:no_json_object")

    if card is None:
        card = CrmCard(request_summary="", confidence=0.0)
        return ExtractionResult(
            card=card,
            output=card.to_output(missing_fields=list(KEY_FIELDS), needs_review=True),
            missing_fields=list(KEY_FIELDS),
            needs_review=True,
            json_valid=False,
            attempts=attempts,
            provider=provider.config.provider,
            model=provider.config.model,
            usage=completion.usage,
            latency_s=time.perf_counter() - started,
            issues=issues,
            raw=raw,
        )

    _apply_rules(card, email_text, issues)
    _evidence_check(card, email_text, issues)

    missing = _missing_key_fields(card)
    needs_review = _should_review(card, issues, confidence_threshold, had_error=bool(completion.error))

    return ExtractionResult(
        card=card,
        output=card.to_output(missing_fields=missing, needs_review=needs_review),
        missing_fields=missing,
        needs_review=needs_review,
        json_valid=True,
        attempts=attempts,
        provider=provider.config.provider,
        model=provider.config.model,
        usage=completion.usage,
        latency_s=time.perf_counter() - started,
        issues=issues,
        raw=completion.content,
    )


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "EVIDENCE_FIELDS",
    "extract_card",
    "parse_json_object",
]

