"""Тесты оценки «сути запроса» (метрики + судья) — без сети."""
from __future__ import annotations

from eval.judge import judge_summary
from eval.metrics import summary_stats
from llm.env import ProviderConfig
from llm.providers import Completion
from llm.providers import Usage
from pipeline.rules import coverage

CFG = ProviderConfig(
    provider="openai", tier="expensive", model="gpt-6-sol", base_url="http://fake", api_key="sk-test12345678"
)


class FakeJudge:
    """Провайдер-заглушка: отдаёт заранее заданный ответ."""

    def __init__(self, content: str):
        self.config = CFG
        self._content = content

    def complete(self, messages):  # noqa: ARG002
        return Completion(
            content=self._content, usage=Usage(prompt_tokens=100, completion_tokens=20), latency_s=0.2, status=200
        )


# --------------------------- coverage / summary_stats --------------------------- #
def test_coverage_fully_and_partially_covered():
    # все значимые токены эталона отражены (с учётом словоформ) -> 1.0
    assert coverage("поставка ноутбуков", "нужно КП на поставку ноутбуков") == 1.0
    # ничего не отражено -> 0.0, и это ниже порога
    assert coverage("запрос на поставку ноутбуков", "совсем другое письмо про кофе") < 0.5


def test_summary_stats_nonempty_and_no_judge():
    items = [
        {"pred": {"request_summary": "нужно КП на поставку ноутбуков"}, "gold": {"request_summary": "запрос на поставку ноутбуков"}},
        {"pred": {"request_summary": ""}, "gold": {"request_summary": "запрос на консультацию"}},
    ]
    s = summary_stats(items)
    assert s["nonempty_rate"]["value"] == 0.5
    assert 0.0 <= s["coverage_mean"] <= 1.0
    assert s["judge"] is None


def test_summary_stats_judge_scores():
    items = [
        {"pred": {"request_summary": "x"}, "gold": {"request_summary": "x"}, "judge_score": 5},
        {"pred": {"request_summary": "x"}, "gold": {"request_summary": "x"}, "judge_score": 3},
    ]
    s = summary_stats(items)
    assert s["judge"]["mean"] == 4.0
    assert s["judge"]["share_ge4"] == 0.5
    assert s["judge"]["n"] == 2


# --------------------------- judge_summary --------------------------- #
def test_judge_summary_parses_score():
    provider = FakeJudge('{"score": 5, "invented": [], "rationale": "ok"}')
    v = judge_summary("письмо", "pred", "gold", provider)
    assert v["score"] == 5 and v["error"] == ""
    assert v["usage"]["prompt_tokens"] == 100


def test_judge_summary_clamps_and_captures_invented():
    provider = FakeJudge('{"score": 9, "invented": ["100 тонн"], "rationale": "выдумка"}')
    v = judge_summary("письмо", "pred", "gold", provider)
    assert v["score"] == 5  # зажимаем в 1..5
    assert v["invented"] == ["100 тонн"]


def test_judge_summary_broken_json_safe():
    provider = FakeJudge("не json")
    v = judge_summary("письмо", "pred", "gold", provider)
    assert v["score"] is None and v["error"]
