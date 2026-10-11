#!/usr/bin/env python3
"""
Промпты прототипа «Письмо -> карточка CRM».

Два независимых промпта:
    1. EXTRACTION — DeepSeek достаёт карточку из письма (рабочий путь).
    2. GENERATION — вторая LLM генерирует синтетические письма + эталонную разметку
       (для честного eval: генератор != экстрактор).

Промпты собраны так, чтобы результат был «машинно-проверяемым»:
обязательные поля, enum для срочности, `evidence`-цитаты и `confidence`.
"""
from __future__ import annotations

import json

from pipeline.schema import CARD_SCHEMA_HINT
from pipeline.schema import URGENCIES


def _schema_lines() -> str:
    return "\n".join(f'  - "{k}": {v}' for k, v in CARD_SCHEMA_HINT.items())


EXTRACTION_SYSTEM = f"""Ты — ассистент отдела продаж. Твоя задача: из текста входящего письма \
аккуратно извлечь данные для карточки CRM и вернуть СТРОГО один JSON-объект.

Поля карточки:
{_schema_lines()}

Жёсткие правила:
1. Не выдумывай данные. Если в письме нет значения — верни null и впиши имя поля в "missing_fields".
2. Для КАЖДОГО заполненного поля (кроме confidence/missing_fields/evidence) приведи короткую
   дословную цитату из письма в объекте "evidence" (ключ — имя поля).
3. Срочность "urgency" — только одно из: {", ".join(URGENCIES)}.
   high  — есть слова "срочно/asap/сегодня" или срок <= 3 дней;
   low   — прямо сказано "не срочно" / срок около месяца и более;
   medium — остальное.
4. Язык: верни код языка письма ("ru", "en", ...).
5. confidence — твоя честная уверенность 0..1 в корректности всей карточки.
6. request_summary — 1-2 предложения по сути обращения, без домыслов.
7. Ответ — ТОЛЬКО JSON, без markdown, без пояснений."""


def build_extraction_messages(email_text: str) -> list[dict]:
    """Сообщения для извлечения карточки из одного письма."""
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {
            "role": "user",
            "content": (
                "--- ПИСЬМО (начало) ---\n"
                f"{email_text.strip()}\n"
                "--- ПИСЬМО (конец) ---\n\n"
                "Верни JSON карточки и добавь массив \"missing_fields\"."
            ),
        },
    ]


def build_repair_messages(email_text: str, bad_output: str, error: str) -> list[dict]:
    """Повтор при битом JSON: просим исправить строго по схеме."""
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {
            "role": "user",
            "content": (
                "--- ПИСЬМО (начало) ---\n"
                f"{email_text.strip()}\n"
                "--- ПИСЬМО (конец) ---\n\n"
                "Предыдущий ответ не прошёл валидацию.\n"
                f"Ошибка: {error}\n"
                "Верни ТОЛЬКО валидный JSON-объект строго по схеме, без пояснений."
            ),
        },
    ]


# --------------------------------------------------------------------------- #
# Генерация синтетического golden set
# --------------------------------------------------------------------------- #
#: покрытие краевых случаев, которые обязаны попасть в eval
GENERATION_KINDS = (
    "полное письмо со всеми данными",
    "письмо без названия компании (контакт есть, компании нет)",
    "неоднозначная срочность (без прямых слов, срок 'в течение недели')",
    "не-заявка: спам/рассылка/партнёрское предложение",
    "письмо на английском языке",
    "пересланная ветка (Fwd: ... с несколькими цитатами)",
    "просьба о КП с конкретным дедлайном (высокая срочность)",
    "жалоба на текущий заказ (не новая продажа)",
)


GENERATION_SYSTEM = """Ты — генератор синтетических тестовых данных для задачи \
"извлечение данных из письма в карточку CRM".

Сгенерируй письма на русском (и одно на английском), максимально реалистичные, \
без реальных персональных данных (придумай несуществующие компании, имена, телефоны с \
использованием диапазонов 555, домены example.com). Каждое письмо должно быть про СВОЮ, \
не повторяющуюся компанию, человека и продукт — не копируй один и тот же шаблон.

Для каждого письма верни:
  - "email_text": полный текст письма (может содержать приветствие, подпись, тему);
  - "labels": эталонная карточка с полями: company, contact_name, contact_email,
    contact_phone, product, urgency, requested_deadline, language, request_summary.

Жёсткие правила разметки (важно, эталон должен быть согласован с текстом письма):
  1. Значения company, contact_name, contact_email, contact_phone, product ОБЯЗАНЫ дословно
     встречаться в email_text. Если чего-то в письме нет — ставь null (проверка "не выдумывай").
  2. urgency обязателен и равен РОВНО одному из: "low" | "medium" | "high" (никогда не null).
  3. requested_deadline указывай (YYYY-MM-DD) ТОЛЬКО если в тексте письма есть явная дата
     (например "15 октября 2023" или "20.10.2023"). Если срок относительный
     ("до конца недели", "в течение месяца", "как можно скорее") — ставь null.
  4. language — код языка письма ("ru"/"en"). request_summary — краткое резюме.

Верни СТРОГО JSON-объект вида {"items": [ {"email_text": "...", "labels": {...}}, ... ]}.
Никакого markdown и пояснений."""


def build_generation_messages(n: int, kinds: list[str] | None = None) -> list[dict]:
    """Сообщения для генерации `n` синтетических писем + эталонов."""
    kinds = kinds or list(GENERATION_KINDS)
    chosen = [kinds[i % len(kinds)] for i in range(max(1, n))]
    listing = "\n".join(f"{i + 1}. {k}" for i, k in enumerate(chosen))
    return [
        {"role": "system", "content": GENERATION_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Сгенерируй {n} писем, по одному на каждый тип из списка "
                "(если писем больше, чем типов — варьируй детали):\n"
                f"{listing}\n\n"
                f'Ответ: {{"items": [...]}} ровно из {n} элементов.'
            ),
        },
    ]


def dumps_compact(obj: object) -> str:
    """Компактный JSON (для передачи предыдущего ответа в repair-промпт)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "EXTRACTION_SYSTEM",
    "GENERATION_KINDS",
    "GENERATION_SYSTEM",
    "build_extraction_messages",
    "build_generation_messages",
    "build_repair_messages",
    "dumps_compact",
]
