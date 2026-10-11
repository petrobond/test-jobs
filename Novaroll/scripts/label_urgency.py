#!/usr/bin/env python3
"""
Ре-разметка срочности в golden set «объективным» лаблером (по умолчанию — gpt-6-sol).

Мы уже нашли, что срочность в наборе содержала противоречивые метки. Здесь мы задаём
**единую рубрику** (``pipeline.rules.URGENCY_RUBRIC``) и размечаем ею сильной моделью,
делая эталон согласованным. Извлечение (deepseek-flash) потом сверяется с этой разметкой.

Пример:
    python scripts/label_urgency.py --provider openai --tier expensive
    python scripts/label_urgency.py --provider deepseek --tier cheap   # быстрый дешёвый вариант
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
from config.settings import LABELS_PATH  # noqa: E402
from llm.env import load_env  # noqa: E402
from llm.providers import build_provider  # noqa: E402
from pipeline.extract import parse_json_object  # noqa: E402
from pipeline.rules import URGENCY_RUBRIC  # noqa: E402

BATCH_SIZE = 8

LABELER_SYSTEM = f"""Ты — объективный разметчик срочности входящих писем для CRM.

Применяй ОДНУ рубрику к каждому письму строго по порядку (первое сработавшее правило выигрывает):

{URGENCY_RUBRIC}

Верни СТРОГО JSON-объект: {{"items": [{{"id": "...", "urgency": "low|medium|high"}}, ...]}}.
Ровно по одному элементу на каждое письмо из входа. Никакого markdown и пояснений."""


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def label_batch(provider, batch: list[dict]) -> dict[str, str]:
    """Один вызов лаблера на пачку писем -> {id: urgency}."""
    listing = "\n\n".join(f'id={r["id"]}\n{r["text"]}' for r in batch)
    messages = [
        {"role": "system", "content": LABELER_SYSTEM},
        {"role": "user", "content": f"Разметь срочность для писем:\n\n{listing}"},
    ]
    completion = provider.complete(messages)
    if completion.error or completion.empty:
        print(f"  ! ошибка разметки: {completion.error or 'empty'}", file=sys.stderr)
        return {}
    payload = parse_json_object(completion.content) or {}
    out: dict[str, str] = {}
    for item in payload.get("items", []):
        if isinstance(item, dict) and item.get("id") in {r["id"] for r in batch}:
            val = str(item.get("urgency", "")).strip().lower()
            if val in ("low", "medium", "high"):
                out[item["id"]] = val
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Ре-разметка срочности по рубрике")
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--tier", default="expensive")
    args = parser.parse_args()

    env = load_env()
    provider = build_provider(args.provider, args.tier, env, max_tokens=4096)
    emails = load_jsonl(EMAILS_PATH)
    labels = {r["id"]: r for r in load_jsonl(LABELS_PATH)}
    print(f"Лаблер: {provider.label} | писем: {len(emails)}")

    changed = 0
    for i in range(0, len(emails), BATCH_SIZE):
        batch = emails[i : i + BATCH_SIZE]
        mapping = label_batch(provider, batch)
        print(f"  размечено {len(mapping)} из {len(batch)}")
        for eid, urgency in mapping.items():
            old = (labels.get(eid) or {}).get("labels", {}).get("urgency")
            labels[eid].setdefault("labels", {})
            labels[eid]["labels"]["urgency"] = urgency
            changed += 1 if old != urgency else 0

    with LABELS_PATH.open("w", encoding="utf-8") as fh:
        for eid in [r["id"] for r in emails]:
            fh.write(json.dumps(labels[eid], ensure_ascii=False) + "\n")

    print(f"Готово: обновлено {changed} меток срочности -> {LABELS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
