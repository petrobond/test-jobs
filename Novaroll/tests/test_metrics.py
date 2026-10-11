"""Тесты метрик eval (без сети)."""
from __future__ import annotations

from eval.metrics import field_match
from eval.metrics import hallucination_rate
from eval.metrics import percentiles
from eval.metrics import rate
from eval.metrics import summarize
from eval.metrics import urgency_report


def test_field_match_email_and_phone():
    assert field_match("contact_email", "A@B.com", "a@b.com")
    assert field_match("contact_phone", "+7 999 123-45-67", "8 999 1234567")
    assert not field_match("contact_phone", "+7 999 123-45-67", "+7 999 000-00-00")


def test_field_match_soft_for_company():
    assert field_match("company", "Ромашка", "ООО Ромашка-Логистик")
    assert not field_match("company", "Ромашка", "Василёк")


def test_rate_and_wilson():
    r = rate(9, 10)
    assert r["value"] == 0.9
    assert 0.0 <= r["ci_low"] <= 0.9 <= r["ci_high"] <= 1.0


def test_hallucination_rate_flags_unsupported():
    items = [{"pred": {"company": "ООО Невидимка"}, "source": "тут нет такой компании"}]
    h = hallucination_rate(items)
    assert h["count"] == 1 and h["total"] == 1


def test_percentiles():
    p = percentiles([1.0, 2.0, 3.0, 4.0])
    assert p["max"] == 4.0 and p["mean"] == 2.5


def test_urgency_macro_f1_perfect():
    items = [
        {"gold": {"urgency": "low"}, "pred": {"urgency": "low"}},
        {"gold": {"urgency": "medium"}, "pred": {"urgency": "medium"}},
        {"gold": {"urgency": "high"}, "pred": {"urgency": "high"}},
    ]
    report = urgency_report(items)
    assert report["macro_f1"] == 1.0


def test_summarize_smoke():
    items = [
        {
            "pred": {"company": "Ромашка", "urgency": "high"},
            "gold": {"company": "Ромашка", "urgency": "high"},
            "source": "компания Ромашка, срочно",
            "needs_review": False,
            "json_valid": True,
            "latency_s": 1.2,
            "usage": {"prompt_tokens": 100, "output_tokens": 50},
        }
    ]
    summary = summarize(items, price_in=0.15, price_out=0.60)
    assert summary["n"] == 1
    assert summary["json_valid_rate"]["value"] == 1.0
    assert summary["cost"]["per_email_usd"] > 0
