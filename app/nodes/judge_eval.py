"""리포트 품질 judge — 결정론적 품질 게이트.

RISK_FORCE_JUDGE_FAIL 환경변수(정수 N)만큼 강제로 실패시켜
judge 재작성 루프를 시연할 수 있다. N회 실패 후에는 실제 품질 게이트 결과를 따른다.

현재 단계에서는 외부 LLM을 호출하지 않는다. 지표·재현성 hash·설명·검증 인용의
형태를 결정론적으로 점검해, CI와 로컬 스켈레톤 실행이 외부 키 없이도 재현 가능하게
동작하도록 한다.
"""
import os

from app.state import RiskState

FORCE_FAIL_ENV = "RISK_FORCE_JUDGE_FAIL"


def _safe_int_env(name: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _has_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _verified_citations(citations: list) -> list[dict]:
    return [
        c for c in citations
        if isinstance(c, dict)
        and c.get("verified") is True
        and _has_text(c.get("quote"))
        and _has_text(c.get("source"))
        and _has_text(c.get("chunk_id"))
    ]


def _invalid_citations(citations: list) -> list[dict]:
    return [
        c for c in citations
        if not (
            isinstance(c, dict)
            and c.get("verified") is True
            and _has_text(c.get("quote"))
            and _has_text(c.get("source"))
            and _has_text(c.get("chunk_id"))
        )
    ]


def _build_checks(state: RiskState) -> list[dict]:
    metrics = state.get("metrics") or {}
    explanations = state.get("explanations") or []
    citations = state.get("citations") or []
    approval = state.get("approval") or {}
    meta = metrics.get("meta") or {}

    explanation_texts = [
        e.get("text", "") for e in explanations
        if isinstance(e, dict) and e.get("topic") != "재작성 반영"
    ]
    verified = _verified_citations(citations)
    invalid = _invalid_citations(citations)

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
            "passed": bool(explanation_texts) and all(_has_text(t) for t in explanation_texts),
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
            "required": False,
            "detail": f"검증 통과 인용 {len(verified)}건",
        },
        {
            "name": "approval_locked",
            "passed": approval.get("status") == "locked",
            "required": False,
            "detail": "HITL 승인 잠금 상태",
        },
    ]


def _score(checks: list[dict]) -> float:
    if not checks:
        return 0.0
    return round(sum(1 for c in checks if c["passed"]) / len(checks), 2)


def _manual_review_flags(checks: list[dict]) -> list[str]:
    flags: list[str] = []
    for check in checks:
        if not check["passed"] and not check["required"]:
            flags.append(check["detail"])
    return flags


def _failure_reasons(checks: list[dict]) -> list[str]:
    return [
        check["detail"] for check in checks
        if check["required"] and not check["passed"]
    ]


def judge_eval(state: RiskState) -> dict:
    retries = (state.get("judge_retries") or 0) + 1
    force_fail_n = _safe_int_env(FORCE_FAIL_ENV)
    checks = _build_checks(state)
    score = _score(checks)
    failures = _failure_reasons(checks)
    manual_review_flags = _manual_review_flags(checks)

    if retries <= force_fail_n:
        reason = f"[강제실패 {retries}/{force_fail_n}] judge 재작성 루프 시연"
        return {
            "judge_retries": retries,
            "judge": {
                "passed": False,
                "score": min(score, 0.4),
                "reason": reason,
                "checks": checks,
                "manual_review_flags": manual_review_flags,
            },
            "judge_feedback": reason,
        }

    passed = not failures
    reason = (
        "필수 품질 점검 통과"
        if passed
        else "필수 품질 점검 실패: " + "; ".join(failures)
    )
    feedback = "" if passed else reason

    return {
        "judge_retries": retries,
        "judge": {
            "passed": passed,
            "score": score,
            "reason": reason,
            "checks": checks,
            "manual_review_flags": manual_review_flags,
        },
        "judge_feedback": feedback,
    }
