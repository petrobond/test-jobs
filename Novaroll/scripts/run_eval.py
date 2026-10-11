#!/usr/bin/env python3
"""
Прогон eval по golden set: метрики качества, работы флага review, латентности и стоимости.

Читает `data/golden_set/{emails,labels}.jsonl`, прогоняет каждое письмо через экстрактор
(DeepSeek), сверяет с эталоном и печатает отчёт + пишет `data/eval_report.json`.

Пример:
    python scripts/run_eval.py
    python scripts/run_eval.py --limit 10
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import EMAILS_PATH  # noqa: E402
from config.settings import EVAL_REPORT_PATH  # noqa: E402
from config.settings import LABELS_PATH  # noqa: E402
from config.settings import Settings  # noqa: E402
from config.settings import price_for  # noqa: E402
from eval.judge import judge_summary  # noqa: E402
from eval.metrics import check_targets  # noqa: E402
from eval.metrics import cost_usd  # noqa: E402
from eval.metrics import summarize  # noqa: E402
from llm.env import load_env  # noqa: E402
from llm.providers import build_provider  # noqa: E402
from pipeline.extract import extract_card  # noqa: E402
from pipeline.schema import EVAL_FIELDS  # noqa: E402
from pipeline.schema import SUMMARY_FIELD  # noqa: E402


def load_jsonl(path: Path) -> dict[str, dict]:
    """Читает jsonl в словарь по id."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        out[rec["id"]] = rec
    return out


def build_items(settings: Settings, limit: int | None) -> list[dict]:
    """Прогоняет экстрактор по golden set и собирает элементы для метрик."""
    emails = load_jsonl(EMAILS_PATH)
    labels = load_jsonl(LABELS_PATH)
    if not emails:
        raise SystemExit(f"Нет данных: {EMAILS_PATH}. Сначала запустите scripts/gen_synthetic.py")
    provider = settings.extractor()
    print(f"Экстрактор: {provider.label} | писем: {len(emails)}")
    items: list[dict] = []
    ids = list(emails)[: limit or len(emails)]
    for i, eid in enumerate(ids, 1):
        email = emails[eid]
        gold = (labels.get(eid) or {}).get("labels") or {}
        res = extract_card(email["text"], provider, confidence_threshold=settings.confidence_threshold)
        items.append(
            {
                "id": eid,
                "source": email["text"],
                "gold": gold,
                "pred": {f: res.output.get(f) for f in (*EVAL_FIELDS, SUMMARY_FIELD)},
                "needs_review": res.needs_review,
                "json_valid": res.json_valid,
                "confidence": res.card.confidence,
                "latency_s": res.latency_s,
                "issues": res.issues,
                "usage": {
                    "prompt_tokens": res.usage.prompt_tokens,
                    "output_tokens": res.usage.output_tokens,
                },
            }
        )
        flag = "review" if res.needs_review else "ok    "
        print(f"  [{i}/{len(ids)}] {eid} {flag} {res.latency_s:5.1f}s json={res.json_valid}")
    return items


def run_judge(items: list[dict], provider) -> dict:
    """Оценивает `request_summary` независимым судьёй; пишет score в каждый item."""
    print(f"Судья summary: {provider.label} | писем: {len(items)}")
    price_in, price_out = price_for(provider.config.model)
    cost = 0.0
    scored = 0
    invented_total = 0
    for i, it in enumerate(items, 1):
        pred = (it.get("pred") or {}).get(SUMMARY_FIELD) or ""
        gold = (it.get("gold") or {}).get(SUMMARY_FIELD) or ""
        verdict = judge_summary(it.get("source", ""), pred, gold, provider)
        it["judge_score"] = verdict["score"]
        it["judge_invented"] = verdict["invented"]
        it["judge_rationale"] = verdict["rationale"]
        usage = verdict.get("usage") or {}
        cost += cost_usd(usage.get("prompt_tokens", 0), usage.get("output_tokens", 0), price_in, price_out)
        if verdict["score"] is not None:
            scored += 1
        invented_total += len(verdict["invented"])
        err = f" err={verdict['error'][:40]}" if verdict["error"] else ""
        print(f"  [{i}/{len(items)}] score={verdict['score']}{err}")
    return {
        "model": provider.config.model,
        "scored": scored,
        "invented_total": invented_total,
        "cost_usd": cost,
    }


def print_report(summary: dict, checks: dict) -> None:
    print("\n=== Итоги eval ===")
    print(f"писем: {summary['n']} | JSON-валидных: {summary['json_valid_rate']['value']:.0%}")
    print("\nТочность по полям (accuracy / recall):")
    for field in EVAL_FIELDS:
        pf = summary["per_field"][field]
        print(f"  {field:<20} {pf['accuracy']['value']:.0%} / {pf['recall']['value']:.0%}  (в эталоне: {pf['gold_present']})")
    print(f"\nполнота (общая recall): {summary['field_recall']:.0%}")
    print(f"macro-F1 срочности: {summary['urgency']['macro_f1']:.2f}")
    print(
        f"галлюцинации: {summary['hallucination']['value']:.1%} "
        f"({summary['hallucination']['count']}/{summary['hallucination']['total']})"
    )
    sm = summary["summary"]
    judge_txt = f"{sm['judge']['mean']:.2f}/5" if sm["judge"] else "N/A (--judge)"
    print(
        f"суть запроса: покрытие {sm['coverage_mean']:.0%}, непустых {sm['nonempty_rate']['value']:.0%}, "
        f"судья {judge_txt}"
    )
    rev = summary["review"]
    fp = rev["flag_precision"]["value"]
    fp_txt = f"{fp:.0%}" if (rev["flagged_total"] > 0 and rev["errors_total"] > 0) else "N/A"
    print(
        f"review: помечено {rev['flag_rate']['value']:.0%} карточек, "
        f"точность флага {fp_txt}, "
        f"ложные тревоги {rev['false_alarm_rate']['value']:.0%}, "
        f"слепая зона {rev['blind_spot']['value']:.0%}"
    )
    lat = summary["latency"]
    print(f"латентность: p50 {lat['p50']:.1f}s | p95 {lat['p95']:.1f}s")
    print(f"стоимость: ${summary['cost']['per_email_usd']:.5f}/письмо (${summary['cost']['total_usd']:.4f} всего)")
    print("\nСверка с целями:")
    for name, chk in checks.items():
        if name == "_all_pass":
            continue
        mark = "OK " if chk["pass"] else "!! "
        if chk.get("informational"):
            mark = "i  "
        note = f"  ({chk['note']})" if chk.get("note") else ""
        print(f"  {mark}{name:<32} факт={chk['actual']:.4f} цель={chk['target']}{note}")
    print(f"\nИТОГ: {'ВСЕ ЦЕЛИ ДОСТИГНУТЫ' if checks['_all_pass'] else 'ЕСТЬ ПРОМАХИ'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Прогон eval по golden set")
    parser.add_argument("--limit", type=int, default=None, help="ограничить число писем")
    parser.add_argument("--out", default=str(EVAL_REPORT_PATH), help="куда писать отчёт")
    parser.add_argument(
        "--from-items",
        default=None,
        help="пересчитать метрики из сохранённых карточек (data/eval_items.jsonl) без вызовов API",
    )
    parser.add_argument("--judge", action="store_true", help="оценить request_summary независимым LLM-судьёй")
    parser.add_argument("--judge-provider", default="openai", help="провайдер судьи (по умолчанию openai)")
    parser.add_argument("--judge-tier", default="expensive", help="тир судьи (по умолчанию expensive)")
    args = parser.parse_args()

    settings = Settings.from_env(load_env())
    if args.from_items:
        items = [
            json.loads(line)
            for line in Path(args.from_items).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        print(f"Пересчёт метрик из {args.from_items}: писем {len(items)}")
    else:
        items = build_items(settings, args.limit)
    judge_meta = None
    if args.judge:
        judge_provider = build_provider(args.judge_provider, args.judge_tier, settings.env, max_tokens=1024)
        judge_meta = run_judge(items, judge_provider)
    provider_model = settings.extractor().config.model
    price_in, price_out = price_for(provider_model)
    summary = summarize(items, price_in, price_out)
    checks = check_targets(summary)
    summary["config"] = settings.as_dict()
    summary["judge_meta"] = judge_meta
    summary["checks"] = checks
    summary["examples"] = [
        {"id": it["id"], "pred": it["pred"], "gold": it["gold"], "needs_review": it["needs_review"]}
        for it in items[:5]
    ]
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    items_path = Path(args.out).with_name("eval_items.jsonl")
    with items_path.open("w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    print_report(summary, checks)
    print(f"\nОтчёт: {args.out}\nКарточки: {items_path}")
    return 0 if checks["_all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
