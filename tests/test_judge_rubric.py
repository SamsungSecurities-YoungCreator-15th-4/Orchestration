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
    passed, reason = source_validity([VERIFIED_CITATION], strict=True)
    assert passed is True
    assert reason == "출처 정책 게이트 충족: 검증 통과 인용 1건"
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


def test_numeric_consistency_accepts_cited_evidence_fact_outside_metrics():
    topic = "거시환경·스트레스 개연성"
    text = "한국은행은 2026-05-29 기준금리를 2.50%로 유지했습니다."
    explanations = [{"topic": topic, "text": text, "revision": 0}]
    citations = [
        {
            "claim": topic,
            "quote": text,
            "source": "bok_mpd_202605.pdf",
            "chunk_id": "bok_mpd_202605.pdf::0001",
            "verified": True,
        }
    ]

    passed, reason = numeric_consistency(
        explanations,
        METRICS,
        {AS_OF_DATE},
        citations,
    )

    assert passed is True
    assert "evidence_fact=2" in reason


def test_numeric_consistency_rejects_uncited_or_cross_topic_evidence_fact():
    topic = "거시환경·스트레스 개연성"
    text = "한국은행은 기준금리를 2.50%로 유지했습니다."
    explanations = [{"topic": topic, "text": text, "revision": 0}]
    citations = [
        {
            "claim": "세무 참고",
            "quote": text,
            "source": "bok_mpd_202605.pdf",
            "chunk_id": "bok_mpd_202605.pdf::0001",
            "verified": True,
        }
    ]

    passed, reason = numeric_consistency(
        explanations,
        METRICS,
        {AS_OF_DATE},
        citations,
    )

    assert passed is False
    assert "같은 topic의 검증 인용에 없음" in reason


def test_numeric_consistency_rejects_number_that_is_only_substring_of_cited_fact():
    topic = "거시환경·스트레스 개연성"
    explanations = [{"topic": topic, "text": "시장 참고 금액은 50,000원입니다.", "revision": 0}]
    citations = [
        {
            "claim": topic,
            "quote": "시장 참고 금액은 150,000원입니다.",
            "source": "macro.pdf",
            "chunk_id": "macro.pdf::0001",
            "verified": True,
        }
    ]

    passed, reason = numeric_consistency(explanations, METRICS, {AS_OF_DATE}, citations)

    assert passed is False
    assert "50,000원가 같은 topic의 검증 인용에 없음" in reason


def test_numeric_consistency_accepts_equivalent_cited_currency_units():
    topic = "거시환경·스트레스 개연성"
    explanations = [{"topic": topic, "text": "시장 참고 금액은 0.5억원입니다.", "revision": 0}]
    citations = [
        {
            "claim": topic,
            "quote": "시장 참고 금액은 5,000만원입니다.",
            "source": "macro.pdf",
            "chunk_id": "macro.pdf::0001",
            "verified": True,
        }
    ]

    passed, reason = numeric_consistency(explanations, METRICS, {AS_OF_DATE}, citations)

    assert passed is True
    assert "evidence_fact=1" in reason


def test_numeric_consistency_accepts_equivalent_cited_bp_and_percent():
    topic = "거시환경·스트레스 개연성"
    explanations = [{"topic": topic, "text": "정책금리 충격은 250bp입니다.", "revision": 0}]
    citations = [
        {
            "claim": topic,
            "quote": "정책금리 충격은 2.5%입니다.",
            "source": "macro.pdf",
            "chunk_id": "macro.pdf::0001",
            "verified": True,
        }
    ]

    passed, reason = numeric_consistency(explanations, METRICS, {AS_OF_DATE}, citations)

    assert passed is True
    assert "evidence_fact=1" in reason


def test_numeric_consistency_rejects_same_number_with_different_unit_dimension():
    topic = "거시환경·스트레스 개연성"
    explanations = [{"topic": topic, "text": "참고 금액은 100원입니다.", "revision": 0}]
    citations = [
        {
            "claim": topic,
            "quote": "참고 비율은 100%입니다.",
            "source": "macro.pdf",
            "chunk_id": "macro.pdf::0001",
            "verified": True,
        }
    ]

    passed, reason = numeric_consistency(explanations, METRICS, {AS_OF_DATE}, citations)

    assert passed is False
    assert "100원가 같은 topic의 검증 인용에 없음" in reason


def test_numeric_consistency_does_not_accept_uncited_fact_that_matches_metric_value():
    topic = "거시환경·스트레스 개연성"
    explanations = [
        {
            "topic": topic,
            "text": "참고자료의 정책금리는 1.00%입니다.",
            "revision": 0,
        }
    ]

    passed, reason = numeric_consistency(
        explanations,
        {"confidence": 0.99},
        {AS_OF_DATE},
        [],
    )

    assert passed is False
    assert "1.00%가 같은 topic의 검증 인용에 없음" in reason


def test_numeric_consistency_does_not_accept_uncited_publication_date_matching_as_of_date():
    topic = "거시환경·스트레스 개연성"
    explanations = [
        {
            "topic": topic,
            "text": f"한국은행은 {AS_OF_DATE} 회의에서 정책 방향을 발표했습니다.",
            "revision": 0,
        }
    ]

    passed, reason = numeric_consistency(
        explanations,
        METRICS,
        {AS_OF_DATE},
        [],
    )

    assert passed is False
    assert f"날짜 {AS_OF_DATE}가 같은 topic의 검증 인용에 없음" in reason


def test_numeric_consistency_does_not_reclassify_wrong_var_as_evidence_fact():
    text = f"기준일 {AS_OF_DATE}, 99% 신뢰수준에서 1일 VaR은 4,000만원입니다."
    explanations = [{"topic": "VaR 해석", "text": text, "revision": 0}]
    citations = [
        {
            "claim": "VaR 해석",
            "quote": text,
            "source": "methodology_var_cvar_2026.pdf",
            "chunk_id": "methodology_var_cvar_2026.pdf::0001",
            "verified": True,
        }
    ]

    passed, reason = numeric_consistency(
        explanations,
        METRICS,
        {AS_OF_DATE},
        citations,
    )

    assert passed is False
    assert "4,000만원가 metrics에 없음" in reason


def test_numeric_consistency_does_not_reclassify_wrong_as_of_date_as_evidence_fact():
    wrong_date = "2026-05-29"
    text = f"리포트 기준일은 {wrong_date}입니다."
    explanations = [{"topic": "기준일 및 유의사항", "text": text, "revision": 0}]
    citations = [
        {
            "claim": "기준일 및 유의사항",
            "quote": text,
            "source": "methodology_var_cvar_2026.pdf",
            "chunk_id": "methodology_var_cvar_2026.pdf::0001",
            "verified": True,
        }
    ]

    passed, reason = numeric_consistency(
        explanations,
        METRICS,
        {AS_OF_DATE},
        citations,
    )

    assert passed is False
    assert f"기준 데이터에 없는 날짜 {wrong_date}" in reason


def test_hallucination_pass_and_fail_with_chunk_text():
    passing_llm = _AxisLLM(hallucination_passed=True)
    assert hallucination(
        _explanations("VaR 설명"),
        [VERIFIED_CITATION],
        passing_llm,
        {AS_OF_DATE},
    )[0] is True
    assert VERIFIED_CITATION["extra"]["chunk_text"] in passing_llm.prompts[0]
    assert AS_OF_DATE in passing_llm.prompts[0]

    failing_llm = _AxisLLM(hallucination_passed=False)
    passed, reason = hallucination(
        _explanations("근거 없는 확정적 주장"),
        [VERIFIED_CITATION],
        failing_llm,
    )
    assert passed is False
    assert "hallucination 실패" in reason


def test_hallucination_ignores_non_dict_extra_without_crashing():
    for malformed_extra in ("not-a-dict", None):
        citation = {**VERIFIED_CITATION, "extra": malformed_extra}
        llm = _AxisLLM(hallucination_passed=True)

        passed, _reason = hallucination(
            _explanations("VaR 설명"),
            [citation],
            llm,
        )

        assert passed is True
        assert '"chunk_text": ""' in llm.prompts[0]


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

    passed, _ = prohibited_expression(
        _explanations("수익이 보장됩니다. 투자 권유가 아니며 실제 결과와 다를 수 있습니다.")
    )
    assert passed is False

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
        "수익률을 보장하지 않는다고, 오해해서는 안 됩니다.",
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


def test_old_house_view_adds_non_blocking_freshness_warning():
    state = _normal_state()
    route = {
        "topic": "VaR 설명",
        "category": "house_view",
        "evidence_role": "interpretation_reference",
        "routing_reason": "CVaR 기여도 1위 자산군: 국내주식(domestic_equity)",
    }
    state["run_config"]["audit"] = {
        "llm": {
            "rag_cite": {
                "latest": {
                    "routing_contract": "rag-routing-v1",
                    "routes": [route],
                }
            }
        }
    }
    state["citations"] = [
        {
            **VERIFIED_CITATION,
            "extra": {
                **VERIFIED_CITATION["extra"],
                "category": "house_view",
                "evidence_role": "interpretation_reference",
                "routing_reason": route["routing_reason"],
                "published_at": "2025-05-01",
            },
        }
    ]

    out = judge_eval(state, llm=_AxisLLM())

    freshness = next(
        check
        for check in out["judge"]["checks"]
        if check["name"] == "citation_publication_freshness"
    )
    assert out["judge"]["passed"] is True
    assert freshness["passed"] is False
    assert freshness["required"] is False
    assert any("최신성 중대 경고" in flag for flag in out["judge"]["manual_review_flags"])


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
