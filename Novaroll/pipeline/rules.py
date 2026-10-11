#!/usr/bin/env python3
"""
Детерминированные правила и нормализация (без LLM).

Зачем: LLM может пропустить очевидные e-mail/телефон/дату или ошибиться в срочности.
Правила — дешёвый и предсказуемый слой, который (а) добирает пропущенное,
(б) служит независимой проверкой значений модели (evidence-check).
"""
from __future__ import annotations

import re
import unicodedata

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
#: телефоны: +7 999 123-45-67, 8 (999) 1234567, +1-202-555-0173 и т.п.
PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-().]{7,}\d)")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

#: слова-маркеры срочности
HIGH_MARKERS = (
    "срочно",
    "asap",
    "как можно скорее",
    "сегодня",
    "срочный",
    "горит",
    "urgent",
    "immediately",
    "дэдлайн",
    "дедлайн",
)
LOW_MARKERS = ("не срочно", "некогда", "в течение месяца", "когда-нибудь", "без спешки", "no rush", "не к спеху")

#: относительные сроки -> оценочная срочность
_DEADLINE_DAYS_RE = re.compile(r"\b([1-9]\d?)\s*(?:дн|день|дня|дней)", re.IGNORECASE)
#: «в течение/до конца (следующей) недели», «на этой неделе»
_WEEK_RE = re.compile(
    r"(?:в течение|до конца|до|через|на)\s+(?:конца\s+)?(?:следующей\s+|этой\s+|текущей\s+)?недел",
    re.IGNORECASE,
)
#: «в течение/до конца (следующего) месяца»
_MONTH_RE = re.compile(
    r"(?:в течение|до конца|через)\s+(?:следующего\s+|этого\s+|конца\s+)?месяц",
    re.IGNORECASE,
)
#: явная дата: "15 октября 2023", "20.10.2023", "20.10.23"
_DATE_RE = re.compile(
    r"\d{1,2}\s*(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)"
    r"|\d{2}\.\d{2}\.\d{4}"
    r"|\d{1,2}\.\d{2}\.\d{2}"
)


def normalize_text(text: str) -> str:
    """Лёгкая нормализация: NFC, ё→е, пробелы, нижний регистр — для сравнения строк."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", text)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s = s.strip().lower().replace("ё", "е")
    return s


def normalize_value(value: object) -> str:
    """Нормализация значения поля перед сравнением с эталоном."""
    if value is None:
        return ""
    s = str(value)
    s = re.sub(r"\s+", " ", s).strip().lower()
    # убираем кавычки/скобки по краям
    s = s.strip("\"'«»“”()[]")
    return s


def find_emails(text: str) -> list[str]:
    """Все e-mail из текста (в порядке появления, без дублей)."""
    seen: list[str] = []
    for m in EMAIL_RE.findall(text or ""):
        low = m.lower()
        if low not in seen:
            seen.append(low)
    return seen


def find_phones(text: str) -> list[str]:
    """Все телефоны из текста (нормализованы к цифрам)."""
    seen: list[str] = []
    for m in PHONE_RE.findall(text or ""):
        digits = re.sub(r"\D", "", m)
        if 8 <= len(digits) <= 15 and digits not in seen:
            seen.append(digits)
    return seen


def find_phones_raw(text: str) -> list[str]:
    """Телефоны в исходном формате (для заполнения карточки), отфильтрованные по длине."""
    seen: list[str] = []
    for m in PHONE_RE.findall(text or ""):
        s = m.strip()
        digits = re.sub(r"\D", "", s)
        if 8 <= len(digits) <= 15 and s not in seen:
            seen.append(s)
    return seen


def phones_equal(a: str | None, b: str | None) -> bool:
    """Сравнение телефонов по последним 10 цифрам (страна/формат не важны)."""
    da = re.sub(r"\D", "", a or "")
    db = re.sub(r"\D", "", b or "")
    if not da or not db:
        return da == db
    return da[-10:] == db[-10:]


def contains_evidence(text: str, value: object) -> bool:
    """Встречается ли значение (нормализованное) в исходном письме."""
    needle = normalize_value(value)
    if not needle:
        return True  # нечего проверять
    hay = normalize_text(text)
    if needle in hay:
        return True
    # для телефонов/чисел сравниваем по цифрам
    digits = re.sub(r"\D", "", needle)
    if digits and len(digits) >= 7:
        return digits[-10:] in re.sub(r"\D", "", hay)
    # по токенам: все значимые слова value должны присутствовать (с учётом словоформ)
    tokens = [t for t in re.findall(r"[a-zа-я0-9]+", needle) if len(t) >= 3]
    return bool(tokens) and all(_word_present(t, hay) for t in tokens)


def _word_present(token: str, hay: str) -> bool:
    """Токен есть в тексте, допуская словоизменение (грубая стемминг-эвристика)."""
    if token in hay:
        return True
    # обрезаем 1-3 окончания: "ноутбуки" -> "ноутбук" (есть в "ноутбуков")
    for cut in (1, 2, 3):
        stem = token[: len(token) - cut]
        if len(stem) >= 4 and stem in hay:
            return True
    return False


_SIG_TOKEN_RE = re.compile(r"[a-zа-я0-9]+")


def significant_tokens(text: str, min_len: int = 5) -> list[str]:
    """Значимые токены текста (длина >= min_len), без дублей — для оценки summary."""
    out: list[str] = []
    for tok in _SIG_TOKEN_RE.findall(normalize_text(text)):
        if len(tok) >= min_len and tok not in out:
            out.append(tok)
    return out


def coverage(gold_text: str, pred_text: str, min_len: int = 5) -> float:
    """
    Доля значимых токенов эталона, отражённых в предсказании (прокси полноты summary).

    Сравнение с учётом словоформ (`_word_present`). 1.0 — всё покрыто, 0.0 — ничего.
    """
    toks = significant_tokens(gold_text, min_len)
    if not toks:
        return 1.0
    hay = normalize_text(pred_text)
    return sum(1 for t in toks if _word_present(t, hay)) / len(toks)


URGENCY_RUBRIC = """Рубрика срочности (порядок важен — первое сработавшее правило выигрывает):
1. явное «не срочно / не к спеху / когда угодно» -> low;
2. срок в днях («в течение N дней»): N <= 3 -> high, 4..14 -> medium, > 14 -> low;
3. явные слова срочности («срочно / срочный / asap / urgent / немедленно / как можно скорее») -> high;
4. срок в неделях («на/до этой недели», «в течение недели») -> medium;
5. срок в месяцах («в течение/до конца месяца») -> low;
6. явная календарная дата-дедлайн -> medium (без привязки к «сегодня»);
7. сигналов срочности нет -> low (дефолт)."""


def urgency_from_rules(text: str) -> str:
    """
    Регламентная оценка срочности по рубрике ``URGENCY_RUBRIC``.

    Всегда возвращает значение (дефолт — ``low``): срочность в CRM задаётся правилом,
    а не «мнением модели». См. ``llm/prompts.py`` — тот же порядок описан в промпте.
    """
    hay = normalize_text(text)
    if any(m in hay for m in LOW_MARKERS):
        return "low"
    days = [int(x) for x in _DEADLINE_DAYS_RE.findall(hay)]
    if days:
        d = min(days)
        if d <= 3:
            return "high"
        if d <= 14:
            return "medium"
        return "low"
    if any(m in hay for m in HIGH_MARKERS):
        return "high"
    if _WEEK_RE.search(hay):
        return "medium"
    if _MONTH_RE.search(hay):
        return "low"
    if _DATE_RE.search(hay):
        return "medium"
    return "low"


def guess_language(text: str) -> str:
    """Очень грубая эвристика языка: кириллица -> ru, иначе en."""
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text or "")
    if not letters:
        return "ru"
    cyr = sum(1 for c in letters if "а" <= c.lower() <= "я" or c in "ё")
    return "ru" if cyr / len(letters) > 0.3 else "en"


__all__ = [
    "EMAIL_RE",
    "PHONE_RE",
    "URL_RE",
    "URGENCY_RUBRIC",
    "contains_evidence",
    "coverage",
    "find_emails",
    "find_phones",
    "find_phones_raw",
    "guess_language",
    "normalize_text",
    "normalize_value",
    "phones_equal",
    "significant_tokens",
    "urgency_from_rules",
]
