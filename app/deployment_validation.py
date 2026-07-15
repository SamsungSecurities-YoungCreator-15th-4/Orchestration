"""실제 배포 그래프 결과의 제출·시연 계약을 결정론적으로 검증한다."""

from __future__ import annotations

from dataclasses import dataclass


EXPECTED_CITATION_CATEGORIES = frozenset(
    {"methodology", "macro", "house_view", "tax"}
)
EXPECTED_GRAPH_NODES = (
    "load_inputs",
    "extract_ips",
    "conflict_check",
    "approval_gate",
    "var_engine",
    "rag_cite",
    "judge_eval",
    "assemble_report",
)


@dataclass(frozen=True)
class DeploymentCheck:
    name: str
    passed: bool
    detail: str


def _check(name: str, passed: bool, detail: str) -> DeploymentCheck:
    return DeploymentCheck(name=name, passed=passed, detail=detail)


def _citation_categories(citations: list) -> set[str]:
    categories: set[str] = set()
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        extra = citation.get("extra")
        if isinstance(extra, dict) and isinstance(extra.get("category"), str):
            categories.add(extra["category"])
    return categories


def validate_deployment_state(final: dict, order: list[str]) -> list[DeploymentCheck]:
    """그래프 최종 State에서 배포 필수 조건을 순수 함수로 판정한다."""
    report = final.get("report") if isinstance(final.get("report"), dict) else {}
    governance = (
        report.get("governance")
        if isinstance(report.get("governance"), dict)
        else {}
    )
    run_config = (
        final.get("run_config")
        if isinstance(final.get("run_config"), dict)
        else {}
    )
    observability = (
        run_config.get("observability")
        if isinstance(run_config.get("observability"), dict)
        else {}
    )
    citations = final.get("citations") if isinstance(final.get("citations"), list) else []
    judge = final.get("judge") if isinstance(final.get("judge"), dict) else {}
    judge_checks = judge.get("checks") if isinstance(judge.get("checks"), list) else []
    required_checks = [
        item
        for item in judge_checks
        if isinstance(item, dict) and item.get("required") is True
    ]
    required_failures = [
        item.get("name", "unknown")
        for item in required_checks
        if item.get("passed") is not True
    ]
    categories = _citation_categories(citations)
    missing_categories = sorted(EXPECTED_CITATION_CATEGORIES - categories)
    invalid_citations = sum(
        1
        for citation in citations
        if not isinstance(citation, dict) or citation.get("verified") is not True
    )
    phases = (
        governance.get("langsmith_trace_urls")
        if isinstance(governance.get("langsmith_trace_urls"), dict)
        else {}
    )
    missing_phases = [
        phase
        for phase in ("input", "analysis")
        if not isinstance(phases.get(phase), str) or not phases[phase].startswith("https://")
    ]
    privacy = (
        governance.get("langsmith_privacy")
        if isinstance(governance.get("langsmith_privacy"), dict)
        else {}
    )
    approval = (
        final.get("approval")
        if isinstance(final.get("approval"), dict)
        else {}
    )
    missing_nodes = [node for node in EXPECTED_GRAPH_NODES if node not in order]
    strict_config = run_config.get("strict_citation_gate") is True
    strict_report = governance.get("strict_citation_gate") is True

    return [
        _check(
            "strict citation gate",
            strict_config and strict_report,
            f"state={strict_config}, report={strict_report}",
        ),
        _check(
            "graph E2E nodes",
            not missing_nodes,
            "전체 8노드 실행" if not missing_nodes else f"누락: {', '.join(missing_nodes)}",
        ),
        _check(
            "verified citations",
            bool(citations) and invalid_citations == 0,
            f"검증 인용 {len(citations)}건, 비검증 {invalid_citations}건",
        ),
        _check(
            "four-category citation coverage",
            not missing_categories,
            (
                "methodology, macro, house_view, tax"
                if not missing_categories
                else f"누락: {', '.join(missing_categories)}"
            ),
        ),
        _check(
            "Judge required checks",
            judge.get("passed") is True
            and bool(required_checks)
            and not required_failures,
            (
                f"필수 {len(required_checks)}개 통과, score={judge.get('score')}"
                if judge.get("passed") is True
                and required_checks
                and not required_failures
                else (
                    "필수 검사 없음"
                    if not required_checks
                    else f"실패: {', '.join(required_failures) or 'judge.passed=false'}"
                )
            ),
        ),
        _check(
            "HITL approval lock",
            approval.get("status") == "locked",
            str(approval.get("status")),
        ),
        _check(
            "LangSmith tracing",
            observability.get("langsmith_enabled") is True and not missing_phases,
            (
                "input·analysis trace URL 생성"
                if not missing_phases and observability.get("langsmith_enabled") is True
                else f"누락 phase: {', '.join(missing_phases) or 'tracing disabled'}"
            ),
        ),
        _check(
            "LangSmith privacy masking",
            privacy.get("hide_inputs") is True and privacy.get("hide_outputs") is True,
            f"inputs={privacy.get('hide_inputs')}, outputs={privacy.get('hide_outputs')}",
        ),
    ]


def format_deployment_checks(checks: list[DeploymentCheck]) -> str:
    lines = [
        f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}"
        for check in checks
    ]
    passed = all(check.passed for check in checks)
    lines.append(f"DEPLOYMENT_VALIDATION: {'PASS' if passed else 'FAIL'}")
    return "\n".join(lines)
