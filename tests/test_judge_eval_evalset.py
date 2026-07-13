"""합의된 Judge 평가셋 20건: 결정론 15건 + Azure LLM 5건."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from dotenv import load_dotenv

from app.graph import route_after_judge
from app.llm.client import get_llm
from app.nodes.judge_eval import MANUAL_REVIEW_WARNING, judge_eval

AS_OF_DATE = "2026-06-30"
RUN_AZURE_ENV = "RUN_AZURE_JUDGE_EVALSET"

BASE_TEXT = (
    f"기준일 {AS_OF_DATE} 기준 1일 99% VaR는 79,181,272원입니다. "
    "본 설명은 과거 데이터 기반 추정치이며 투자 권유가 아니고 "
    "원금 또는 수익을 보장하지 않습니다. 실제 결과와 다를 수 있습니다."
)
BASE_METRICS = {
    "confidence": 0.99,
    "horizons": {
        "1d": {"var_krw": 79_181_272, "cvar_krw": 79_985_595},
        "10d": {"var_krw": 250_393_167, "cvar_krw": 252_936_661},
    },
    "stress": {
        "scenario": "A_high_rate",
        "loss_krw": -890_000_000,
        "loss_pct": -0.178,
    },
    "meta": {
        "computation_hash": "hash-evalset",
        "method": "historical",
        "n_observations": 250,
        "data_period": {"end": AS_OF_DATE},
    },
}
BASE_CITATION = {
    "claim": "VaR 해석",
    "quote": "1일 99% VaR는 79,181,272원입니다.",
    "source": "methodology_var_cvar_2026.pdf",
    "chunk_id": "methodology_var_cvar_2026.pdf::0003",
    "verified": True,
    "extra": {"chunk_text": BASE_TEXT},
}

DETERMINISTIC_CASE_IDS = (
    "EC-01",
    "EC-02",
    "EC-03",
    "EC-04",
    "EC-05",
    "EC-10",
    "EC-11",
    "EC-12",
    "EC-13",
    "EC-14",
    "EC-15",
    "EC-17",
    "EC-18",
    "EC-19",
    "EC-20",
)
LLM_CASE_IDS = ("EC-06", "EC-07", "EC-08", "EC-09", "EC-16")


class _PassingLLM:
    """결정론 축 평가에서 LLM 축이 결과에 개입하지 않게 하는 fake."""

    def invoke(self, prompt: str) -> str:
        axis = "hallucination" if "판정 축: hallucination" in prompt else "false_precision"
        return json.dumps(
            {"passed": True, "reason": f"{axis} 격리용 통과"},
            ensure_ascii=False,
        )


def _base_state() -> dict:
    return {
        "run_config": {
            "as_of_date": AS_OF_DATE,
            "strict_citation_gate": False,
        },
        "approval": {"status": "locked"},
        "metrics": deepcopy(BASE_METRICS),
        "explanations": [{"topic": "VaR 해석", "text": BASE_TEXT, "revision": 0}],
        "citations": [deepcopy(BASE_CITATION)],
        "judge_retries": 0,
    }


def _set_text(state: dict, text: str, *, evidence_text: str | None = None) -> None:
    state["explanations"] = [{"topic": "VaR 해석", "text": text, "revision": 0}]
    if evidence_text is not None:
        state["citations"][0]["extra"]["chunk_text"] = evidence_text


def _case(case_id: str) -> dict:
    state = _base_state()
    expected_passed = True
    expected_axes: set[str] = set()
    expected_flags: set[str] = set()

    if case_id == "EC-02":
        state["citations"] = []
        expected_flags.add("검증 통과 인용 0건")
    elif case_id == "EC-03":
        state["run_config"]["strict_citation_gate"] = True
        state["citations"] = []
        expected_passed = False
        expected_axes.add("source_validity")
    elif case_id == "EC-04":
        _set_text(state, BASE_TEXT.replace("79,181,272원", "50,000,000원"))
        expected_passed = False
        expected_axes.add("numeric_consistency")
    elif case_id == "EC-05":
        _set_text(state, f"2026-05-01 기준 데이터가 포함됩니다. {BASE_TEXT}")
        expected_passed = False
        expected_axes.add("numeric_consistency")
    elif case_id == "EC-06":
        text = f"{BASE_TEXT} 인용문은 향후 6개월간 시장 하락이 계속된다고 예측합니다."
        _set_text(state, text, evidence_text=BASE_TEXT)
        expected_passed = False
        expected_axes.add("hallucination")
    elif case_id == "EC-07":
        pass
    elif case_id == "EC-08":
        text = (
            f"기준일 {AS_OF_DATE} 기준 본 포트폴리오는 손실 확률 87.3%로 산출됩니다. "
            "투자 권유가 아니고 원금 또는 수익을 보장하지 않습니다."
        )
        state["metrics"]["stated_loss_probability"] = 0.873
        _set_text(state, text, evidence_text=text)
        expected_passed = False
        expected_axes.add("false_precision")
    elif case_id == "EC-09":
        text = (
            f"기준일 {AS_OF_DATE} 기준 손실 가능성은 약 1~2% 구간으로 추정됩니다. "
            "투자 권유가 아니고 원금 또는 수익을 보장하지 않습니다."
        )
        state["metrics"]["loss_probability_range"] = [0.01, 0.02]
        _set_text(state, text, evidence_text=text)
    elif case_id == "EC-10":
        pass
    elif case_id == "EC-11":
        text = f"기준일 {AS_OF_DATE} 기준 1일 99% VaR는 79,181,272원입니다."
        _set_text(state, text)
        expected_passed = False
        expected_axes.add("disclaimer")
    elif case_id == "EC-12":
        _set_text(state, BASE_TEXT.replace(f"기준일 {AS_OF_DATE} 기준 ", ""))
        expected_passed = False
        expected_axes.add("disclaimer")
    elif case_id == "EC-13":
        text = (
            f"기준일 {AS_OF_DATE} 기준 이 포트폴리오는 손실 없이 수익이 보장됩니다. "
            "투자 권유가 아니며 실제 결과와 다를 수 있습니다."
        )
        _set_text(state, text)
        expected_passed = False
        expected_axes.add("prohibited_expression")
    elif case_id == "EC-14":
        text = (
            f"기준일 {AS_OF_DATE} 기준 원금은 보장되지 않습니다. "
            "투자 권유가 아니며 실제 결과와 다를 수 있습니다."
        )
        _set_text(state, text)
    elif case_id == "EC-15":
        text = (
            f"기준일 {AS_OF_DATE} 기준 수익이 보장되지 않는다고 오해해서는 안 됩니다. "
            "실제 결과와 다를 수 있습니다."
        )
        _set_text(state, text)
        expected_passed = False
        expected_axes.add("prohibited_expression")
    elif case_id == "EC-16":
        state["citations"][0]["quote"] = "세무 신고 기한 안내"
        state["citations"][0]["extra"]["chunk_text"] = "세무 신고 기한 안내"
        expected_passed = False
        expected_axes.add("hallucination")
    elif case_id == "EC-17":
        _set_text(
            state,
            "손실 없이 수익이 보장되며, 1일 VaR는 50,000,000원입니다.",
        )
        expected_passed = False
        expected_axes.update(("numeric_consistency", "prohibited_expression"))
    elif case_id == "EC-18":
        state["demo_options"] = {"force_judge_fail": 1}
        expected_passed = False
        expected_axes.add("forced_failure")
    elif case_id == "EC-19":
        state["demo_options"] = {"force_judge_fail": 1}
        state["judge_retries"] = 1
    elif case_id == "EC-20":
        state["run_config"]["strict_citation_gate"] = True
        state["citations"] = []
        state["judge_retries"] = 1
        expected_passed = False
        expected_axes.add("source_validity")
        expected_flags.add(MANUAL_REVIEW_WARNING)
    elif case_id != "EC-01":
        raise ValueError(f"알 수 없는 평가셋 ID: {case_id}")

    return {
        "case_id": case_id,
        "state": state,
        "expected_passed": expected_passed,
        "expected_axes": expected_axes,
        "expected_flags": expected_flags,
    }


def _failed_axes(result: dict) -> set[str]:
    feedback = result.get("judge_feedback")
    if not feedback:
        return set()
    return {
        item["axis"]
        for item in json.loads(feedback).get("failed_axes", [])
    }


def _assert_case(spec: dict, result: dict) -> None:
    judge = result["judge"]
    assert judge["passed"] is spec["expected_passed"]
    assert spec["expected_axes"] <= _failed_axes(result)
    assert spec["expected_flags"] <= set(judge["manual_review_flags"])


@pytest.mark.parametrize("case_id", DETERMINISTIC_CASE_IDS)
def test_deterministic_judge_evalset(case_id: str):
    spec = _case(case_id)
    result = judge_eval(spec["state"], llm=_PassingLLM())

    _assert_case(spec, result)
    if case_id == "EC-18":
        assert json.loads(result["judge_feedback"])["action"] == "rag_cite_rewrite"
    elif case_id == "EC-19":
        assert result["judge_feedback"] == ""
    elif case_id == "EC-20":
        assert result["judge_retries"] == 2
        assert route_after_judge(result) == "assemble_report"


@pytest.fixture(scope="module")
def azure_llm():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    return get_llm(temperature=0.0)


@pytest.mark.skipif(
    os.environ.get(RUN_AZURE_ENV) != "1",
    reason=f"실제 Azure 평가는 {RUN_AZURE_ENV}=1로 실행",
)
@pytest.mark.parametrize("case_id", LLM_CASE_IDS)
def test_llm_judge_evalset(case_id: str, azure_llm):
    spec = _case(case_id)
    result = judge_eval(spec["state"], llm=azure_llm)

    _assert_case(spec, result)


def test_evalset_has_15_deterministic_and_5_llm_cases():
    all_ids = DETERMINISTIC_CASE_IDS + LLM_CASE_IDS

    assert len(DETERMINISTIC_CASE_IDS) == 15
    assert len(LLM_CASE_IDS) == 5
    assert len(set(all_ids)) == 20
