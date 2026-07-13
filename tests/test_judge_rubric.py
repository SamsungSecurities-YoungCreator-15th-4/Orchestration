"""judge_eval 6축 루브릭 단위·E2E 테스트 — 실제 Azure 호출 없음."""
from __future__ import annotations

import json

from app.graph import MAX_JUDGE_RETRIES, route_after_judge
from app.judge.rubric import (
    disclaimer,
    false_precision,
    hallucination,
    numeric_consistency,
    prohibited_expression,
    prohibited_manual_flags,
    source_validity,
)
from app.nodes.assemble_report import assemble_report
from app.nodes.judge_eval import MANUAL_REVIEW_WARNING, judge_eval

AS_OF_DATE = "2026-06-30"
DISCLAIMER_TEXT = (
    f"기준일 {AS_OF_DATE}의 과거 데이터 기반 추정치이며 투자 권유가 아니고, "
    "원금 또는 수익을 보장하지 않습니다. 실제 결과와 다를 수 있습니다."
)
METRICS = {
    "confidence": 0.99,
    "horizons": {"1d": {"var_krw": 30_000_000}},
    "meta": {
        "computation_hash": "metric-hash",
        "data_period": {"end": AS_OF_DATE},
    },
}
VERIFIED_CITATION = {
    "claim": "VaR 설명",
    "quote": "99% 신뢰수준의 VaR은 손실 추정치다.",
    "source": "methodology.pdf",
    "chunk_id": "methodology.pdf::0001",
    "verified": True,
    "extra": {"chunk_text": "99% 신뢰수준의 VaR은 손실 추정치다."},
}


class _AxisLLM:
    def __init__(self, *, hallucination_passed: bool = True, precision_passed: bool = True):
        self.answers = {
            "hallucination": hallucination_passed,
            "false_precision": precision_passed,
        }
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        axis = next(name for name in self.answers if f"판정 축: {name}" in prompt)
        passed = self.answers[axis]
        return json.dumps(
            {
                "passed": passed,
                "reason": f"{axis} {'통과' if passed else '실패'}",
            },
            ensure_ascii=False,
        )


def _explanations(text: str) -> list[dict]:
    return [{"topic": "설명", "text": text, "revision": 0}]


def _normal_state() -> dict:
    return {
        "run_config": {"as_of_date": AS_OF_DATE, "strict_citation_gate": True},
        "approval": {"status": "locked"},
        "metrics": METRICS,
        "explanations": _explanations(DISCLAIMER_TEXT),
        "citations": [VERIFIED_CITATION],
    }


def test_source_validity_pass_and_fail():
    assert source_validity([VERIFIED_CITATION], strict=True)[0] is True
    assert source_validity([], strict=False)[0] is True
    passed, reason = source_validity([], strict=True)
    assert passed is False
    assert "0건" in reason


def test_numeric_consistency_pass_and_fail():
    good = _explanations(
        f"기준일 {AS_OF_DATE}, 99% 신뢰수준에서 1일 VaR은 약 3,000만원입니다."
    )
    assert numeric_consistency(good, METRICS, {AS_OF_DATE})[0] is True

    bad = _explanations(
        f"기준일 {AS_OF_DATE}, 99% 신뢰수준에서 1일 VaR은 4,000만원입니다."
    )
    passed, reason = numeric_consistency(bad, METRICS, {AS_OF_DATE})
    assert passed is False
    assert "4,000만원" in reason


def test_numeric_consistency_preserves_confidence_key_for_list_values():
    explanations = _explanations("99% 신뢰수준은 약 100일 중 1일의 초과를 뜻합니다.")

    assert numeric_consistency(
        explanations,
        {"confidence": [0.99]},
    )[0] is True


def test_numeric_consistency_ignores_unitless_ordinals_and_counts():
    explanations = _explanations("2가지 요인 중 1순위 위험을 설명합니다.")

    assert numeric_consistency(explanations, {})[0] is True


def test_hallucination_pass_and_fail_with_chunk_text():
    passing_llm = _AxisLLM(hallucination_passed=True)
    assert hallucination(_explanations("VaR 설명"), [VERIFIED_CITATION], passing_llm)[0] is True
    assert VERIFIED_CITATION["extra"]["chunk_text"] in passing_llm.prompts[0]

    failing_llm = _AxisLLM(hallucination_passed=False)
    passed, reason = hallucination(
        _explanations("근거 없는 확정적 주장"),
        [VERIFIED_CITATION],
        failing_llm,
    )
    assert passed is False
    assert "hallucination 실패" in reason


def test_false_precision_pass_and_fail():
    passing_llm = _AxisLLM(precision_passed=True)
    assert false_precision(
        _explanations("99% 신뢰수준에서 1일 VaR은 약 3,000만원입니다."),
        passing_llm,
    )[0] is True

    failing_llm = _AxisLLM(precision_passed=False)
    passed, reason = false_precision(
        _explanations("손실 확률은 2.3%입니다."),
        failing_llm,
    )
    assert passed is False
    assert "false_precision 실패" in reason


def test_disclaimer_pass_and_fail():
    assert disclaimer(_explanations(DISCLAIMER_TEXT), {AS_OF_DATE})[0] is True
    passed, reason = disclaimer(_explanations("VaR 설명입니다."), {AS_OF_DATE})
    assert passed is False
    assert "기준일" in reason
    assert "면책" in reason


def test_prohibited_expression_negated_pass_and_positive_fail():
    assert prohibited_expression(_explanations("원금은 보장되지 않습니다."))[0] is True
    assert prohibited_expression(_explanations("원금은 보장 안 됨을 명시합니다."))[0] is True

    passed, reason = prohibited_expression(_explanations("수익률을 보장합니다."))
    assert passed is False
    assert "보장" in reason

    passed, _ = prohibited_expression(_explanations("수익률을 보장 안정적으로 제공합니다."))
    assert passed is False


def test_prohibited_expression_ambiguous_negations_request_manual_review():
    explanations = _explanations("수익을 보장하지 않거나 못 한다고 봅니다.")
    passed, reason = prohibited_expression(explanations)
    assert passed is True
    assert "수동검토" in reason
    assert prohibited_manual_flags(explanations)


def test_prohibited_expression_clear_double_negation_fails():
    texts = (
        "수익률을 보장하지 않는다고 오해해서는 안 됩니다.",
        "수익을 보장하지 않을 수 없습니다.",
        "수익 보장이 아니라고 할 수 없습니다.",
        "수익을 보장하지 않는다고 착각하지 마십시오.",
    )
    for text in texts:
        explanations = _explanations(text)
        passed, reason = prohibited_expression(explanations)

        assert passed is False
        assert "명시적 이중부정" in reason
        assert prohibited_manual_flags(explanations) == []


def test_judge_eval_normal_e2e_passes_with_fake_llm():
    out = judge_eval(_normal_state(), llm=_AxisLLM())

    assert out["judge"]["passed"] is True
    assert out["judge_feedback"] == ""
    assert set(out["judge"]["rubric"]) == {
        "source_validity",
        "numeric_consistency",
        "hallucination",
        "false_precision",
        "disclaimer",
        "prohibited_expression",
    }
    assert all(axis["passed"] for axis in out["judge"]["rubric"].values())


def test_judge_retry_limit_exits_with_manual_review_warning():
    state = _normal_state()
    failing_llm = _AxisLLM(hallucination_passed=False)

    first = judge_eval(state, llm=failing_llm)
    first_state = {**state, **first}
    assert route_after_judge(first_state) == "rag_cite"

    second = judge_eval(first_state, llm=failing_llm)
    exhausted_state = {**first_state, **second}
    assert exhausted_state["judge_retries"] == MAX_JUDGE_RETRIES
    assert route_after_judge(exhausted_state) == "assemble_report"
    assert MANUAL_REVIEW_WARNING in second["judge"]["manual_review_flags"]
    assert MANUAL_REVIEW_WARNING in assemble_report(exhausted_state)["report"]["warnings"]
