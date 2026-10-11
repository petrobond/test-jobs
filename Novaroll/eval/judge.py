#!/usr/bin/env python3
"""
LLM-судья для оценки поля `request_summary` (свободный текст).

Строгое сравнение для «сути запроса» не работает (ответ — парафраз), поэтому независимая
модель оценивает предсказанное резюме по рубрике 1..5 на фоне исходного письма и эталона.
Судья — НЕ экстрактор: извлечение делает DeepSeek, судью берём отдельно (по умолчанию `gpt-6-sol`).
"""
from __future__ import annotations

from llm.providers import ChatProvider
from pipeline.extract import parse_json_object

JUDGE_SYSTEM = """Ты — строгий и объективный оценщик краткого резюме письма для CRM.

Тебе дают: исходное письмо, эталонное резюме и резюме, сгенерированное системой.
Оцени сгенерированное резюме по трём критериям, затем поставь ЦЕЛОСТНУЮ оценку 1..5:
  - фактическая корректность: нет фактов, которых нет в письме (выдумки);
  - полнота: отражены ключевые пункты (зачем пишут и что нужно);
  - краткость: 1-2 предложения, без воды.
Шкала: 5 — полностью корректно и полно; 4 — хорошо, мелкие недочёты; 3 — упущена важная деталь;
2 — существенные пропуски/неточности; 1 — неверно или пусто.

Ответ — СТРОГО JSON: {"score": <целое 1..5>, "invented": ["<выдуманный факт>", ...], "rationale": "<кратко>"}.
Никакого markdown и пояснений."""


def build_judge_messages(email: str, gold_summary: str, pred_summary: str) -> list[dict]:
    """Сообщения для судьи (письмо + эталон + предсказание)."""
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"--- ПИСЬМО ---\n{email.strip()}\n\n"
                f"--- ЭТАЛОННОЕ РЕЗЮМЕ ---\n{gold_summary.strip() or '(нет)'}\n\n"
                f"--- РЕЗЮМЕ СИСТЕМЫ ---\n{pred_summary.strip() or '(пусто)'}\n\n"
                "Оцени и верни JSON."
            ),
        },
    ]


def judge_summary(
    email: str,
    pred_summary: str,
    gold_summary: str,
    provider: ChatProvider,
) -> dict:
    """
    Оценка резюме одной модели-судьёй. Никогда не бросает исключение.

    Возвращает {"score": int|None, "invented": [...], "rationale": str, "error": str}.
    """
    completion = provider.complete(build_judge_messages(email, gold_summary, pred_summary))
    usage = {"prompt_tokens": completion.usage.prompt_tokens, "output_tokens": completion.usage.output_tokens}
    if completion.error or completion.empty:
        return {
            "score": None,
            "invented": [],
            "rationale": "",
            "error": completion.error or "empty content",
            "usage": usage,
        }
    payload = parse_json_object(completion.content)
    if not payload:
        return {"score": None, "invented": [], "rationale": "", "error": "parse: no json object", "usage": usage}
    raw_score = payload.get("score")
    try:
        score = int(round(float(raw_score)))
    except (TypeError, ValueError):
        score = None
    if score is not None:
        score = min(5, max(1, score))
    invented = payload.get("invented") or []
    if not isinstance(invented, list):
        invented = []
    return {
        "score": score,
        "invented": [str(x) for x in invented],
        "rationale": str(payload.get("rationale") or "")[:300],
        "error": "",
        "usage": usage,
    }


__all__ = ["JUDGE_SYSTEM", "build_judge_messages", "judge_summary"]
