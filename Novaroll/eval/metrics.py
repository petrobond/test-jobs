#!/usr/bin/env python3
"""
Метрики eval для кейса «Письмо -> карточка CRM» (без сторонних зависимостей).

Считаем то, что важно для CRM: точность по полям, полноту, галлюцинации,
работу флага `needs_review`, латентность и стоимость. Все пороги-цели заданы
в `TARGETS` и сверяются с фактом в отчёте.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from pipeline.rules import contains_evidence
from pipeline.rules import phones_equal
from pipeline.schema import EVAL_FIELDS

Z_95 = 1.959964

#: поля, значения которых обязаны присутствовать в письме дословно (для ловли галлюцинаций).
#: Служебные поля исключены: `urgency` — enum, `language` — код, `requested_deadline` — ISO-нормализация.
HALLUCINATION_FIELDS = ("company", "contact_name", "contact_email", "contact_phone", "product")

#: целевые пороги (acceptance-критерий «черновик годен»)
TARGETS = {
    "json_valid_rate": 0.98,
    "field_accuracy:company": 0.95,
    "field_accuracy:contact_name": 0.95,
    "field_accuracy:contact_email": 0.95,
    "field_accuracy:contact_phone": 0.90,
    "field_accuracy:product": 0.90,
    "field_accuracy:urgency": 0.90,
    "field_accuracy:requested_deadline": 0.90,
    "field_recall": 0.95,
    "review_recall": 0.90,
    "false_alarm_rate_max": 0.30,
    "hallucination_rate_max": 0.02,
    "latency_p95_max": 8.0,
    "cost_per_email_max": 0.005,
}


# --------------------------------------------------------------------------- #
# Доверительные интервалы / базовые метрики
# --------------------------------------------------------------------------- #
def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson 95% CI для доли (честная оценка при малом n)."""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1.0 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = (z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate(successes: int, total: int) -> dict:
    lo, hi = wilson_interval(successes, total)
    return {
        "count": successes,
        "total": total,
        "value": (successes / total) if total else 0.0,
        "ci_low": lo,
        "ci_high": hi,
    }


def _norm(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().strip("\"'«»“”()[]")


def field_match(field: str, pred: object, gold: object) -> bool:
    """Совпадение значения поля с эталоном (с учётом специфики поля)."""
    if field == "contact_email":
        return _norm(pred).lower() == _norm(gold).lower()
    if field == "contact_phone":
        if not pred and not gold:
            return True
        return phones_equal(str(pred or ""), str(gold or ""))
    if field == "urgency":
        return _norm(pred) == _norm(gold)
    if field == "language":
        return _norm(pred)[:2] == _norm(gold)[:2]
    if field == "requested_deadline":
        return _norm(pred) == _norm(gold)
    # мягкое совпадение: одно значение содержит другое (компания/продукт/имя)
    a, b = _norm(pred), _norm(gold)
    if a == b:
        return True
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    if field in ("company", "product"):
        return _token_overlap(a, b) >= 0.6
    return False


def _token_overlap(a: str, b: str) -> float:
    """Пересечение значимых токенов двух строк, нормированное на меньшую длину."""
    ta = set(re.findall(r"[a-zа-я0-9]+", a or ""))
    tb = set(re.findall(r"[a-zа-я0-9]+", b or ""))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def hallucination_rate(items: list[dict]) -> dict:
    """
    Доля непустых значений, которых нет в исходном письме (по всем evidence-полям).

    `items[i]` = {"pred": card_dict, "source": email_text}.
    """
    total = 0
    bad = 0
    offenders: list[str] = []
    for item in items:
        pred = item.get("pred") or {}
        src = item.get("source") or ""
        for field in HALLUCINATION_FIELDS:
            value = pred.get(field)
            if not value:
                continue
            total += 1
            if field == "contact_phone":
                ok = phones_equal(str(value), src) or contains_evidence(src, value)
            else:
                ok = contains_evidence(src, value)
            if not ok:
                bad += 1
                offenders.append(f"{field}={value!r}")
    result = rate(bad, total)
    result["offenders"] = offenders[:20]
    return result


def per_field_report(items: list[dict]) -> dict:
    """
    Точность и полнота по полям.

    `items[i]` = {"pred": card_dict, "gold": labels_dict}.
    accuracy — совпадение значения (в т.ч. null==null);
    recall — по полям, реально присутствующим в эталоне.
    """
    report: dict = {}
    for field in EVAL_FIELDS:
        correct = 0
        gold_present = 0
        gold_found = 0
        for item in items:
            pred = item.get("pred") or {}
            gold = item.get("gold") or {}
            g = gold.get(field)
            p = pred.get(field)
            if field_match(field, p, g):
                correct += 1
            if g not in (None, "", []):
                gold_present += 1
                if field_match(field, p, g):
                    gold_found += 1
        report[field] = {
            "accuracy": rate(correct, len(items)),
            "recall": rate(gold_found, gold_present),
            "gold_present": gold_present,
        }
    # полнота по ключевым полям в целом
    total_present = sum(report[f]["gold_present"] for f in EVAL_FIELDS)
    total_found = sum(report[f]["recall"]["count"] for f in EVAL_FIELDS)
    report["_overall_recall"] = rate(total_found, total_present)
    return report


def urgency_report(items: list[dict]) -> dict:
    """P/R/F1 по классам срочности (macro-F1) — enum-качество."""
    labels = ("low", "medium", "high")
    gold = [_norm((it.get("gold") or {}).get("urgency")) for it in items]
    pred = [_norm((it.get("pred") or {}).get("urgency")) for it in items]
    out: dict = {"classes": {}, "n": len(gold)}
    f1s = []
    for lab in labels:
        tp = sum(1 for g, p in zip(gold, pred) if g == lab and p == lab)
        fp = sum(1 for g, p in zip(gold, pred) if g != lab and p == lab)
        fn = sum(1 for g, p in zip(gold, pred) if g == lab and p != lab)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        out["classes"][lab] = {"precision": precision, "recall": recall, "f1": f1, "support": gold.count(lab)}
        f1s.append(f1)
    out["macro_f1"] = sum(f1s) / len(f1s) if f1s else 0.0
    return out


def review_stats(items: list[dict]) -> dict:
    """
    Работа флага `needs_review`.

    Ошибка карточки = битый JSON или расхождение хотя бы по одному полю.
    review_recall — доля ошибок, которые попали под флаг (чтобы человек их увидел);
    false_alarm_rate — доля ВЕРНЫХ карточек, зря помеченных (цена лишней ручной работы).
    """
    errors_total = errors_flagged = 0
    correct_total = correct_flagged = 0
    for item in items:
        pred = item.get("pred") or {}
        gold = item.get("gold") or {}
        flagged = bool(item.get("needs_review"))
        is_error = (not item.get("json_valid", True)) or any(
            not field_match(f, pred.get(f), gold.get(f)) for f in EVAL_FIELDS
        )
        if is_error:
            errors_total += 1
            errors_flagged += 1 if flagged else 0
        else:
            correct_total += 1
            correct_flagged += 1 if flagged else 0
    return {
        "review_recall": rate(errors_flagged, errors_total),
        "false_alarm_rate": rate(correct_flagged, correct_total),
        "errors_total": errors_total,
        "correct_total": correct_total,
    }


# --------------------------------------------------------------------------- #
# Латентность и стоимость
# --------------------------------------------------------------------------- #
def percentiles(values: list[float]) -> dict:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "mean": 0.0, "max": 0.0}
    ordered = sorted(values)

    def pick(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(p * len(ordered)))]

    return {"p50": pick(0.5), "p95": pick(0.95), "mean": sum(ordered) / len(ordered), "max": ordered[-1]}


def cost_usd(prompt_tokens: int, output_tokens: int, price_in: float, price_out: float) -> float:
    """Стоимость вызова в USD по цене за 1M токенов."""
    return prompt_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out


def summarize(items: list[dict], price_in: float = 0.0, price_out: float = 0.0) -> dict:
    """Полный отчёт по набору карточек."""
    n = len(items)
    valid = sum(1 for it in items if it.get("json_valid", True))
    latencies = [float(it.get("latency_s", 0.0)) for it in items]
    costs = [
        cost_usd(
            int((it.get("usage") or {}).get("prompt_tokens", 0)),
            int((it.get("usage") or {}).get("output_tokens", 0)),
            price_in,
            price_out,
        )
        for it in items
    ]
    fields = per_field_report(items)
    summary = {
        "n": n,
        "json_valid_rate": rate(valid, n),
        "field_accuracy": {f: fields[f]["accuracy"]["value"] for f in EVAL_FIELDS},
        "field_recall": fields["_overall_recall"]["value"],
        "field_recall_detail": fields["_overall_recall"],
        "per_field": fields,
        "urgency": urgency_report(items),
        "hallucination": hallucination_rate(items),
        "review": review_stats(items),
        "latency": percentiles(latencies),
        "cost": {
            "per_email_usd": (sum(costs) / n) if n else 0.0,
            "total_usd": sum(costs),
            "price_in": price_in,
            "price_out": price_out,
        },
    }
    return summary


def check_targets(summary: dict) -> dict:
    """Сверяет факт с целевыми порогами; возвращает {name: {"target","actual","pass"}}."""
    checks: dict = {}

    def add(name: str, actual: float, target: float, *, maximum: bool = False) -> None:
        ok = actual <= target if maximum else actual >= target
        checks[name] = {"target": target, "actual": actual, "pass": ok}

    add("json_valid_rate", summary["json_valid_rate"]["value"], TARGETS["json_valid_rate"])
    for field, target in TARGETS.items():
        if field.startswith("field_accuracy:"):
            fname = field.split(":", 1)[1]
            add(field, summary["field_accuracy"].get(fname, 0.0), target)
    add("field_recall", summary["field_recall"], TARGETS["field_recall"])
    add("review_recall", summary["review"]["review_recall"]["value"], TARGETS["review_recall"])
    add(
        "false_alarm_rate",
        summary["review"]["false_alarm_rate"]["value"],
        TARGETS["false_alarm_rate_max"],
        maximum=True,
    )
    add("hallucination_rate", summary["hallucination"]["value"], TARGETS["hallucination_rate_max"], maximum=True)
    add("latency_p95", summary["latency"]["p95"], TARGETS["latency_p95_max"], maximum=True)
    add("cost_per_email", summary["cost"]["per_email_usd"], TARGETS["cost_per_email_max"], maximum=True)
    checks["_all_pass"] = all(c["pass"] for c in checks.values())
    return checks


__all__ = [
    "HALLUCINATION_FIELDS",
    "TARGETS",
    "check_targets",
    "cost_usd",
    "field_match",
    "hallucination_rate",
    "per_field_report",
    "percentiles",
    "rate",
    "review_stats",
    "summarize",
    "urgency_report",
    "wilson_interval",
]


