"""Тесты извлечения карточки (LLM мокается — сети нет)."""
from __future__ import annotations

import json

from llm.env import ProviderConfig
from llm.providers import Completion
from llm.providers import Usage
from pipeline.extract import extract_card
from pipeline.extract import parse_json_object

SOURCE = """Здравствуйте! Компания ООО Ромашка-Логистик, контакт Мария Соколова.
Нужно КП на поставку ноутбуков. Пишите m.sokolova@romashka.example.com,
тел. +7 999 123-45-67. Это срочно, срок 2026-10-20."""

CARD = {
    "company": "ООО Ромашка-Логистик",
    "contact_name": "Мария Соколова",
    "contact_email": "m.sokolova@romashka.example.com",
    "contact_phone": "+7 999 123-45-67",
    "request_summary": "Запрос КП на поставку ноутбуков.",
    "product": "ноутбуки",
    "urgency": "high",
    "requested_deadline": "2026-10-20",
    "language": "ru",
    "confidence": 0.95,
    "evidence": {"company": "ООО Ромашка-Логистик"},
}


class FakeProvider:
    """Провайдер-заглушка: отдаёт заранее заданные ответы по порядку."""

    def __init__(self, contents: list[str], error: str = ""):
        self.config = ProviderConfig(
            provider="deepseek", tier="cheap", model="deepseek-flash", base_url="http://x", api_key="k"
        )
        self._contents = list(contents)
        self._error = error
        self.calls = 0

    def complete(self, messages):  # noqa: ARG002
        self.calls += 1
        content = self._contents.pop(0) if self._contents else ""
        return Completion(
            content=content,
            usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            latency_s=0.5,
            status=200 if not self._error else -1,
            error=self._error,
        )


def test_parse_json_object_strips_fences():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('текст {"a": 2} хвост') == {"a": 2}
    assert parse_json_object("не json") is None


def test_valid_card_is_parsed():
    provider = FakeProvider([json.dumps(CARD, ensure_ascii=False)])
    res = extract_card(SOURCE, provider)
    assert res.json_valid
    assert res.output["company"] == "ООО Ромашка-Логистик"
    assert res.output["urgency"] == "high"
    assert res.needs_review is False  # всё заполнено, высокая уверенность, значения подтверждены
    assert provider.calls == 1


def test_rules_fill_missing_email_and_phone():
    partial = dict(CARD)
    partial["contact_email"] = None
    partial["contact_phone"] = None
    provider = FakeProvider([json.dumps(partial, ensure_ascii=False)])
    res = extract_card(SOURCE, provider)
    assert res.output["contact_email"] == "m.sokolova@romashka.example.com"
    assert res.output["contact_phone"] == "+7 999 123-45-67"
    assert any(i.startswith("rule_fallback") for i in res.issues)


def test_unsupported_value_triggers_review():
    bad = dict(CARD)
    bad["company"] = "ООО Невидимка"  # нет в письме
    provider = FakeProvider([json.dumps(bad, ensure_ascii=False)])
    res = extract_card(SOURCE, provider)
    assert res.needs_review is True
    assert any(i == "unsupported_value:company" for i in res.issues)


def test_broken_json_is_repaired():
    provider = FakeProvider(["{битый json", json.dumps(CARD, ensure_ascii=False)])
    res = extract_card(SOURCE, provider)
    assert res.json_valid
    assert res.attempts == 2
    assert provider.calls == 2


def test_llm_error_fails_open_to_review():
    provider = FakeProvider([""], error="net: Timeout")
    res = extract_card(SOURCE, provider)
    assert not res.json_valid
    assert res.needs_review is True
