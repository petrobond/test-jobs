"""Тесты построения карточки в Streamlit-приложении (без запуска Streamlit)."""
from __future__ import annotations

from app.streamlit_app import CARD_TABLE_FIELDS
from app.streamlit_app import build_card_rows


def test_build_card_rows_shapes_and_placeholders():
    output = {
        "company": "ООО Ромашка",
        "contact_name": None,
        "urgency": "high",
        "request_summary": "Суть запроса.",
        "confidence": 0.9,
        "needs_review": True,
        "missing_fields": ["contact_phone"],
    }
    rows = build_card_rows(output)
    labels = {r["Поле"]: r["Значение"] for r in rows}
    assert labels["Компания"] == "ООО Ромашка"
    assert labels["Контакт (имя)"] == "—"  # пусто -> прочерк
    assert labels["Срочность"] == "high"
    assert labels["Проверить вручную"] == "да"
    assert labels["Не найдено"] == "contact_phone"
    assert len(rows) == len(CARD_TABLE_FIELDS)


def test_build_card_rows_empty_missing_fields():
    rows = build_card_rows({"needs_review": False, "missing_fields": []})
    labels = {r["Поле"]: r["Значение"] for r in rows}
    assert labels["Не найдено"] == "—"
    assert labels["Проверить вручную"] == "нет"


def test_build_card_rows_includes_request_summary():
    """«Суть запроса» (поле из ТЗ) обязана быть в таблице карточки."""
    rows = build_card_rows({"request_summary": "Запрос КП на поставку ноутбуков.", "missing_fields": []})
    labels = {r["Поле"]: r["Значение"] for r in rows}
    assert labels["Суть запроса"] == "Запрос КП на поставку ноутбуков."
