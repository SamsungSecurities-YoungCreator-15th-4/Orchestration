"""리포트 품질 judge — 기존 형태 검사와 6축 루브릭을 함께 실행한다.

state.demo_options의 force_judge_fail 값만큼 강제로 실패시켜 judge 재작성 루프를
시연할 수 있다. 환경변수는 이전 호출 방식과의 하위 호환용으로만 읽는다.
N회 실패 후에는 실제 품질 게이트 결과를 따른다.

기존 형태 검사는 결정론적으로 유지하고, LLM은 설명 품질 판정에만 관여하며
수치 계산 경로에는 진입하지 않는다.
"""
from __future__ import annotations

import json
import os

from app.judge.rubric import AXIS_NAMES, evaluate_rubric
from app.state import RiskState

FORCE_FAIL_ENV = "RISK_FORCE_JUDGE_FAIL"
MAX_JUDGE_RETRIES = 2
MANUAL_REVIEW_WARNING = "검증 미통과 — 수동검토 필요"


def _safe_int_env(name: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _has_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_verified_citation(citation) -> bool:
    return (
        isinstance(citation, dict)
        and citation.get("verified") is True
        and _has_text(citation.get("quote"))
        and _has_text(citation.get("source"))
        and _has_text(citation.get("chunk_id"))
    )


def _verified_citations(citations: list) -> list[dict]:
    return [citation for citation in citations if _is_verified_citation(citation)]


def _invalid_citations(citations: list) -> list[dict]:
    return [citation for citation in citations if not _is_verified_citation(citation)]


def _build_checks(state: RiskState) -> list[dict]:
    """기존 7개 형태 검사를 preflight로 보존한다."""
    run_config = state.get("run_config") or {}
    metrics = state.get("metrics") or {}
    explanations = state.get("explanations") or []
    citations = state.get("citations") or []
    approval = state.get("approval") or {}
    meta = metrics.get("meta") or {}

    explanation_texts = [
        explanation.get("text", "")
        for explanation in explanations
        if isinstance(explanation, dict) and explanation.get("topic") != "재작성 반영"
    ]
    verified = _verified_citations(citations)
    invalid = _invalid_citations(citations)
    strict_citation_gate = run_config.get("strict_citation_gate") is True

    return [
        {
            "name": "metrics_present",
            "passed": bool(metrics),
            "required": True,
            "detail": "리스크 지표 존재",
        },
        {
            "name": "computation_hash_present",
            "passed": _has_text(meta.get("computation_hash")),
            "required": True,
            "detail": "재현성 computation_hash 존재",
        },
        {
            "name": "explanations_present",
            "passed": bool(explanations),
            "required": True,
            "detail": f"설명 {len(explanations)}건",
        },
        {
            "name": "explanations_have_text",
            "passed": bool(explanation_texts) and all(_has_text(text) for text in explanation_texts),
            "required": True,
            "detail": "주요 설명 문장 텍스트 존재",
        },
        {
            "name": "citations_all_verified",
            "passed": not invalid,
            "required": True,
            "detail": f"검증 실패/형식 오류 인용 {len(invalid)}건",
        },
        {
            "name": "verified_citations_present",
            "passed": bool(verified),
            "required": strict_citation_gate,
            "detail": f"검증 통과 인용 {len(verified)}건",
        },
        {
            "name": "approval_locked",
            "passed": approval.get("status") == "locked",
            "required": False,
            "detail": "HITL 승인 잠금 상태",
        },
    ]


def _expected_dates(state: RiskState) -> set[str]:
    run_config = state.get("run_config") or {}
    metrics = state.get("metrics") or {}
    data_period = (metrics.get("meta") or {}).get("data_period") or {}
    return {
        str(value)
        for value in (run_config.get("as_of_date"), data_period.get("end"))
        if value
    }


def _rubric_checks(state: RiskState, llm) -> tuple[list[dict], dict, list[str]]:
    run_config = state.get("run_config") or {}
    results, manual_flags = evaluate_rubric(
        explanations=state.get("explanations") or [],
        citations=state.get("citations") or [],
        metrics=state.get("metrics") or {},
        strict_citation_gate=run_config.get("strict_citation_gate") is True,
        expected_dates=_expected_dates(state),
        llm=llm,
    )
    rubric = {
        name: {"passed": results[name][0], "reason": results[name][1]}
        for name in AXIS_NAMES
    }
    checks = [
        {
            "name": name,
            "passed": result[0],
            "required": True,
            "detail": result[1],
        }
        for name, result in results.items()
    ]
    return checks, rubric, manual_flags


def _score(checks: list[dict]) -> float:
    if not checks:
        return 0.0
    return round(sum(1 for check in checks if check["passed"]) / len(checks), 2)


def _manual_review_flags(checks: list[dict]) -> list[str]:
    return [
        check["detail"]
        for check in checks
        if not check["passed"] and not check["required"]
    ]


def _failure_items(checks: list[dict]) -> list[dict]:
    return [
        {"axis": check["name"], "reason": check["detail"]}
        for check in checks
        if check["required"] and not check["passed"]
    ]


def _feedback(retries: int, failures: list[dict]) -> str:
    return json.dumps(
        {
            "action": "rag_cite_rewrite",
            "attempt": retries,
            "failed_axes": failures,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _get_default_llm():
    try:
        from app.llm.client import get_llm

        return get_llm(temperature=0.0)
    except Exception:
        return None


def judge_eval(state: RiskState, *, llm=None) -> dict:
    """형태 검사와 6축 루브릭을 집계한다. llm은 테스트 주입 가능하다."""
    retries = (state.get("judge_retries") or 0) + 1
    demo_options = state.get("demo_options") or {}
    force_fail_n = demo_options.get("force_judge_fail")
    if not isinstance(force_fail_n, int) or isinstance(force_fail_n, bool):
        force_fail_n = _safe_int_env(FORCE_FAIL_ENV)
    judge_llm = llm if llm is not None else _get_default_llm()

    preflight_checks = _build_checks(state)
    rubric_checks, rubric, rubric_manual_flags = _rubric_checks(state, judge_llm)
    checks = preflight_checks + rubric_checks
    score = _score(checks)
    failures = _failure_items(checks)
    manual_review_flags = list(
        dict.fromkeys(_manual_review_flags(preflight_checks) + rubric_manual_flags)
    )

    if retries <= force_fail_n:
        failures = [
            {
                "axis": "forced_failure",
                "reason": f"judge 재작성 루프 시연 {retries}/{force_fail_n}",
            }
        ]

    passed = not failures
    if not passed and retries >= MAX_JUDGE_RETRIES:
        manual_review_flags.append(MANUAL_REVIEW_WARNING)
        manual_review_flags = list(dict.fromkeys(manual_review_flags))

    reason = (
        "필수 품질 점검 통과"
        if passed
        else "필수 품질 점검 실패: "
        + "; ".join(f"{item['axis']}={item['reason']}" for item in failures)
    )
    feedback = "" if passed else _feedback(retries, failures)

    return {
        "judge_retries": retries,
        "judge": {
            "passed": passed,
            "score": score if passed else min(score, 0.4) if retries <= force_fail_n else score,
            "reason": reason,
            "checks": checks,
            "rubric": rubric,
            "manual_review_flags": manual_review_flags,
        },
        "judge_feedback": feedback,
    }
