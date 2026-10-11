"""Тесты детерминированных правил (без сети)."""
from __future__ import annotations

from pipeline.rules import contains_evidence
from pipeline.rules import find_emails
from pipeline.rules import find_phones
from pipeline.rules import guess_language
from pipeline.rules import phones_equal
from pipeline.rules import urgency_from_rules


def test_find_emails_dedup_and_lowercase():
    text = "Пишите на A.Smirnov@Example.com или a.smirnov@example.com"
    assert find_emails(text) == ["a.smirnov@example.com"]


def test_find_phones_normalized():
    assert find_phones("тел. +7 (555) 123-45-67 доб. 12")[0] == "75551234567"


def test_phones_equal_ignores_country_and_format():
    assert phones_equal("+7 (555) 123-45-67", "8 555 1234567")
    assert not phones_equal("+7 555 123-45-67", "+7 555 000-00-00")


def test_urgency_rules():
    assert urgency_from_rules("Это срочно, сделайте сегодня") == "high"
    assert urgency_from_rules("нужно в течение 2 дней") == "high"
    assert urgency_from_rules("не срочно, как будет время") == "low"
    assert urgency_from_rules("обычное письмо без сроков") is None


def test_contains_evidence_handles_russian_forms():
    assert contains_evidence("поставку 50 ноутбуков", "ноутбуки")
    assert contains_evidence("Нужны подшипники SKF серии 6205 на склад", "подшипники SKF серии 6205")
    assert not contains_evidence("в письме про кофе", "квадрокоптеры")


def test_guess_language():
    assert guess_language("Здравствуйте, нужен товар") == "ru"
    assert guess_language("Hello, we need the goods ASAP") == "en"
