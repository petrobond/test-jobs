#!/usr/bin/env python3
"""
Streamlit-демо: письмо -> карточка CRM.

Один экран: слева вставьте письмо, справа получите готовую карточку с уверенностью
и флагом «требует проверки». Вкладка «Eval» показывает метрики по golden set.

Запуск локально:
    streamlit run app/streamlit_app.py
Для Streamlit Cloud: entrypoint `Novaroll/app/streamlit_app.py`, ключи — в Secrets.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# корень проекта (Novaroll/) в sys.path — чтобы импортировались пакеты llm/pipeline/config
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from config.settings import EMAILS_PATH  # noqa: E402
from config.settings import EVAL_REPORT_PATH  # noqa: E402
from config.settings import load_settings  # noqa: E402
from pipeline.extract import extract_card  # noqa: E402
from pipeline.schema import EVAL_FIELDS  # noqa: E402


def _bridge_streamlit_secrets() -> None:
    """Переносит st.secrets в окружение (для деплоя на Streamlit Cloud)."""
    try:
        for key, value in st.secrets.items():  # type: ignore[attr-defined]
            os.environ.setdefault(str(key), str(value))
    except Exception:  # noqa: BLE001 - локально secrets.toml может отсутствовать
        pass


@st.cache_resource(show_spinner=False)
def get_settings():
    """Настройки из окружения (кэшируются на время сессии)."""
    _bridge_streamlit_secrets()
    return load_settings()


def load_examples() -> list[dict]:
    """Примеры писем из golden set (для быстрого демо)."""
    if not EMAILS_PATH.exists():
        return []
    out = []
    for line in EMAILS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_eval_report() -> dict | None:
    """Прошлый отчёт eval (если уже считали)."""
    if not EVAL_REPORT_PATH.exists():
        return None
    try:
        return json.loads(EVAL_REPORT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


FIELD_LABELS = {
    "company": "Компания",
    "contact_name": "Контакт (имя)",
    "contact_email": "E-mail",
    "contact_phone": "Телефон",
    "request_summary": "Суть запроса",
    "product": "Продукт",
    "urgency": "Срочность",
    "requested_deadline": "Срок",
    "language": "Язык",
    "confidence": "Уверенность",
    "missing_fields": "Не найдено",
    "needs_review": "Проверить вручную",
}


#: поля таблицы карточки: поля из ТЗ + служебные (кортеж + кортеж — без смешения с list)
CARD_TABLE_FIELDS = (*EVAL_FIELDS, "confidence", "needs_review", "missing_fields")


def build_card_rows(output: dict) -> list[dict]:
    """Строки таблицы карточки (чистая функция — тестируется без Streamlit)."""
    rows = []
    for field in CARD_TABLE_FIELDS:
        value = output.get(field)
        if field == "missing_fields":
            value = ", ".join(value) if value else "—"
        elif field == "needs_review":
            value = "да" if value else "нет"
        elif value in (None, ""):
            value = "—"
        rows.append({"Поле": FIELD_LABELS.get(field, field), "Значение": value})
    return rows


def render_card(output: dict) -> None:
    """Рисует карточку CRM и служебные сигналы."""
    conf = float(output.get("confidence", 0.0))
    if output.get("needs_review"):
        st.warning("⚠️ Карточка помечена «требует проверки» — проверьте поля перед занесением в CRM.")
    else:
        st.success("✅ Карточка заполнена уверенно — можно заносить в CRM.")
    st.progress(min(max(conf, 0.0), 1.0), text=f"Уверенность модели: {conf:.0%}")

    st.dataframe(pd.DataFrame(build_card_rows(output)), hide_index=True, use_container_width=True)

    evidence = output.get("evidence") or {}
    if evidence:
        with st.expander("Подтверждения (цитаты из письма)"):
            for field, quote in evidence.items():
                st.markdown(f"- **{FIELD_LABELS.get(field, field)}**: «{quote}»")
    with st.expander("JSON карточки (для CRM/интеграции)"):
        st.code(json.dumps(output, ensure_ascii=False, indent=2), language="json")


def tab_extract(provider, settings) -> None:
    """Вкладка одиночного письма."""
    col_in, col_out = st.columns([1, 1])
    examples = load_examples()
    default_text = examples[0]["text"] if examples else ""
    with col_in:
        st.subheader("Вход: письмо")
        if "email_text" not in st.session_state:
            st.session_state["email_text"] = default_text
        if examples:
            idx = st.selectbox(
                "Взять пример из набора",
                options=list(range(len(examples))),
                format_func=lambda i: f"{examples[i]['id']} — {examples[i]['text'][:60]}...",
            )
            if st.button("Загрузить пример"):
                st.session_state["email_text"] = examples[idx]["text"]
        text = st.text_area("Текст письма", key="email_text", height=260)
        run = st.button("Извлечь карточку", type="primary")
    with col_out:
        st.subheader("Выход: карточка CRM")
        if run:
            if not text.strip():
                st.error("Вставьте текст письма.")
            else:
                with st.spinner(f"DeepSeek ({provider.config.model}) извлекает поля..."):
                    result = extract_card(text, provider, confidence_threshold=settings.confidence_threshold)
                st.session_state["last_result"] = result.as_dict()
        result = st.session_state.get("last_result")
        if result:
            render_card(result["card"])
            meta = result["meta"]
            st.caption(
                f"модель {meta['model']} · {meta['latency_s']} c · токенов {meta['usage']['total_tokens']} "
                f"(reasoning {meta['usage']['reasoning_tokens']}) · попыток {meta['attempts']}"
            )
            if meta["issues"]:
                st.caption("Замечания: " + ", ".join(meta["issues"]))
        else:
            st.info("Нажмите «Извлечь карточку», чтобы увидеть результат.")


def tab_eval() -> None:
    """Вкладка с метриками качества (из отчёта scripts/run_eval.py)."""
    report = load_eval_report()
    if not report:
        st.info("Отчёта ещё нет. Запустите `python scripts/run_eval.py` — и метрики появятся здесь.")
        return
    s = report
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Писем в eval", s["n"])
    c2.metric("JSON валидных", f"{s['json_valid_rate']['value']:.0%}")
    c3.metric("Галлюцинации", f"{s['hallucination']['value']:.1%}")
    c4.metric("Стоимость/письмо", f"${s['cost']['per_email_usd']:.5f}")

    st.subheader("Сверка с целями")
    checks = s.get("checks", {})
    rows = [
        {"Метрика": name, "Факт": round(chk["actual"], 4), "Цель": chk["target"], "OK": "✅" if chk["pass"] else "❌"}
        for name, chk in checks.items()
        if name != "_all_pass"
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption(f"Итог: {'ВСЕ ЦЕЛИ ДОСТИГНУТЫ' if checks.get('_all_pass') else 'ЕСТЬ ПРОМАХИ'}")

    st.subheader("Точность по полям")
    pf = s["per_field"]
    fields_df = pd.DataFrame(
        [
            {
                "Поле": FIELD_LABELS.get(f, f),
                "Accuracy": f"{pf[f]['accuracy']['value']:.0%}",
                "Recall": f"{pf[f]['recall']['value']:.0%}",
                "В эталоне": pf[f]["gold_present"],
            }
            for f in EVAL_FIELDS
        ]
    )
    st.dataframe(fields_df, hide_index=True, use_container_width=True)
    lat = s["latency"]
    st.caption(f"Латентность: p50 {lat['p50']:.1f} c · p95 {lat['p95']:.1f} c · модель {s['config']['extract']}")


def tab_about(provider, settings) -> None:
    """Короткая справка о решении."""
    st.markdown(
        f"""
**Задача.** Менеджеры вручную перекладывают данные из писем в CRM и ошибаются.
Прототип достаёт из письма готовые поля карточки.

**Как работает.** DeepSeek ({provider.config.model}) в JSON-режиме возвращает карточку,
дальше — валидация схемой, правила-фолбэки (regex для e-mail/телефона) и evidence-check
(значение без цитаты в письме поднимает флаг «требует проверки»). **Срочность** считается
регламентно — по единой рубрике (правилом, а не «мнением модели»).

**Разные LLM.** Генерация синтетики и извлечение разведены по провайдерам/моделям
(«чистота эксперимента»): сейчас извлечение — `{settings.extract_provider}:{settings.extract_tier}`,
генерация — `{settings.gen_provider}:{settings.gen_tier}`.

**Порог уверенности.** `{settings.confidence_threshold}` — ниже него карточка помечается «проверить».
        """
    )
    st.code(
        "python scripts/gen_synthetic.py --n 16\n"
        "python scripts/run_eval.py\n"
        "streamlit run app/streamlit_app.py",
        language="bash",
    )


def main() -> None:
    st.set_page_config(page_title="Письмо → карточка CRM", page_icon="✉️", layout="wide")
    st.title("✉️ Письмо → карточка CRM")
    st.caption("Прототип: LLM извлекает из письма поля для карточки CRM. Данные синтетические, без ПДн.")

    try:
        settings = get_settings()
        provider = settings.extractor()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Нет доступа к LLM: {exc}. Проверьте DEEPSEEK_API_KEY в .env или Secrets.")
        st.stop()

    tab1, tab2, tab3 = st.tabs(["Извлечь карточку", "Eval", "О решении"])
    with tab1:
        tab_extract(provider, settings)
    with tab2:
        tab_eval()
    with tab3:
        tab_about(provider, settings)


if __name__ == "__main__":
    main()


