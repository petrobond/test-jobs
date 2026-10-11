#!/usr/bin/env python3
"""
Генерация синтетического golden set (письма + эталонная разметка).

Синтетику делает одна LLM (генератор), а поля извлекает другая модель (экстрактор) —
это «чистота эксперимента»: генератор != тестируемая модель. И письма, и разметка
пишутся раздельно: `emails.jsonl` (вход) и `labels.jsonl` (эталон).

Пример:
    python scripts/gen_synthetic.py --n 24 --provider deepseek
    python scripts/gen_synthetic.py --n 24 --provider openai   # когда есть OpenAI-ключ
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
from config.settings import Settings  # noqa: E402
from llm.env import load_env  # noqa: E402
from llm.prompts import build_generation_messages  # noqa: E402
from llm.providers import build_provider  # noqa: E402
from pipeline.extract import parse_json_object  # noqa: E402

BATCH_SIZE = 4  # по столько писем просим за один вызов (иначе ответ не влезает)


def generate_batch(provider, n: int) -> list[dict]:
    """Один вызов генератора на `n` писем; возвращает список {email_text, labels}."""
    messages = build_generation_messages(n)
    completion = provider.complete(messages)
    if completion.error or completion.empty:
        print(f"  ! ошибка генерации: {completion.error or 'empty'}", file=sys.stderr)
        return []
    payload = parse_json_object(completion.content)
    if not payload or not isinstance(payload.get("items"), list):
        print("  ! неожиданный формат ответа генератора", file=sys.stderr)
        return []
    items = [it for it in payload["items"] if isinstance(it, dict) and it.get("email_text")]
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Генерация синтетического golden set")
    parser.add_argument("--n", type=int, default=24, help="сколько писем сгенерировать")
    parser.add_argument("--provider", default=None, help="провайдер генерации (по умолчанию из .env)")
    parser.add_argument("--tier", default=None, help="тир модели (cheap|expensive)")
    parser.add_argument("--out-dir", default=None, help="куда писать (по умолчанию data/golden_set)")
    args = parser.parse_args()

    settings = Settings.from_env(load_env())
    provider_name = args.provider or settings.gen_provider
    tier = args.tier or settings.gen_tier
    provider = build_provider(provider_name, tier, settings.env, max_tokens=4096)
    print(f"Генератор: {provider.label} | нужно писем: {args.n}")

    records: list[dict] = []
    eid = 0
    while len(records) < args.n:
        want = min(BATCH_SIZE, args.n - len(records))
        batch = generate_batch(provider, want)
        print(f"  получено {len(batch)} из {want}")
        if not batch:
            break
        for it in batch:
            if len(records) >= args.n:
                break
            eid += 1
            records.append({"id": f"e{eid:03d}", "text": it["email_text"].strip(), "labels": it.get("labels") or {}})

    if not records:
        print("Не удалось сгенерировать данные.", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir) if args.out_dir else EMAILS_PATH.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    emails_path = out_dir / "emails.jsonl"
    labels_path = out_dir / "labels.jsonl"
    with emails_path.open("w", encoding="utf-8") as fe:
        for rec in records:
            fe.write(json.dumps({"id": rec["id"], "text": rec["text"]}, ensure_ascii=False) + "\n")
    with labels_path.open("w", encoding="utf-8") as fl:
        for rec in records:
            fl.write(json.dumps({"id": rec["id"], "labels": rec["labels"]}, ensure_ascii=False) + "\n")

    print(f"Готово: {len(records)} писем -> {emails_path} и {labels_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
