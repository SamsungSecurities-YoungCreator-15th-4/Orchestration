"""최종 리포트 조립 — 수치·근거·심사·재현성 정보를 한 덩어리로 구성."""
from app.state import RiskState

DISCLAIMER = (
    "본 리포트는 내부 리스크 점검 목적으로 자동 생성된 자료이며, "
    "투자 권유 또는 수익 보장을 의미하지 않습니다. 모든 수치는 "
    "과거 데이터 기반 추정치로 실제 결과와 다를 수 있습니다."
)


def _portfolio_summary(portfolio: list[dict]) -> dict:
    total_value = sum(
        (p.get("value_krw") or 0) if isinstance(p, dict) else 0
        for p in portfolio
    )
    return {
        "total_value_krw": total_value,
        "asset_count": len(portfolio),
        "weights": {
            p.get("asset_class", f"asset_{idx}"): p.get("weight")
            for idx, p in enumerate(portfolio)
            if isinstance(p, dict)
        },
    }


def _compact_stress_scenario(name: str | None, result: dict) -> dict:
    return {
        "scenario": result.get("scenario") or name,
        "description": result.get("description"),
        "reference": result.get("reference"),
        "loss_krw": result.get("loss_krw"),
        "loss_pct": result.get("loss_pct"),
    }


def _stress_summary(stress: dict) -> dict:
    """단일·다중 스트레스 결과를 같은 리포트 요약 계약으로 정규화한다."""
    if not isinstance(stress, dict) or not stress:
        scenarios = []
    elif any(key in stress for key in ("scenario", "loss_krw", "loss_pct")):
        scenarios = [_compact_stress_scenario(stress.get("scenario"), stress)]
    else:
        scenarios = [
            _compact_stress_scenario(str(name), result)
            for name, result in sorted(stress.items(), key=lambda item: str(item[0]))
            if isinstance(result, dict)
        ]

    candidates = [
        scenario
        for scenario in scenarios
        if isinstance(scenario.get("loss_krw"), (int, float))
        and not isinstance(scenario.get("loss_krw"), bool)
    ]
    worst = min(
        candidates,
        key=lambda scenario: (
            -scenario["loss_krw"],
            str(scenario.get("scenario") or ""),
        ),
        default={},
    )
    return {
        "stress_scenario": worst.get("scenario"),
        "stress_loss_krw": worst.get("loss_krw"),
        "stress_loss_pct": worst.get("loss_pct"),
        "stress_scenario_count": len(scenarios),
        "stress_scenarios": scenarios,
    }


def _risk_summary(metrics: dict) -> dict:
    horizons = metrics.get("horizons") or {}
    stress = metrics.get("stress") or {}
    return {
        "confidence": metrics.get("confidence"),
        "var_1d_krw": (horizons.get("1d") or {}).get("var_krw"),
        "cvar_1d_krw": (horizons.get("1d") or {}).get("cvar_krw"),
        "var_10d_krw": (horizons.get("10d") or {}).get("var_krw"),
        "cvar_10d_krw": (horizons.get("10d") or {}).get("cvar_krw"),
        **_stress_summary(stress),
    }


def _evidence_summary(citations: list[dict]) -> dict:
    verified = [
        c for c in citations
        if isinstance(c, dict) and c.get("verified") is True
    ]
    sources = sorted({
        c.get("source", "") for c in verified
        if c.get("source")
    })
    return {
        "citation_count": len(citations),
        "verified_citation_count": len(verified),
        "sources": sources,
        "coverage": "verified" if verified else "not_available",
    }


def _warnings(state: RiskState, evidence: dict) -> list[str]:
    warnings: list[str] = []
    judge = state.get("judge") or {}
    if not judge.get("passed"):
        warnings.append("judge 품질 점검이 통과되지 않았습니다.")
    if evidence["verified_citation_count"] == 0:
        warnings.append("검증 통과 인용이 없어 사람 검토가 필요합니다.")
    if state.get("conflicts"):
        warnings.append("IPS 충돌 이력이 approval에 첨부되어 있습니다.")
    warnings.extend(judge.get("manual_review_flags") or [])
    return list(dict.fromkeys(warnings))


def assemble_report(state: RiskState) -> dict:
    metrics = state.get("metrics") or {}
    meta = metrics.get("meta") or {}
    run_config = state.get("run_config") or {}
    portfolio = state.get("portfolio") or []
    citations = state.get("citations") or []
    evidence = _evidence_summary(citations)
    judge = state.get("judge") or {}
    warnings = _warnings(state, evidence)
    report = {
        "title": "재현가능·설명가능 리스크 리포트",
        "as_of_date": run_config.get("as_of_date"),
        "trace_id": state.get("trace_id"),
        "summary": {
            "portfolio": _portfolio_summary(portfolio),
            "risk": _risk_summary(metrics),
            "judge_passed": judge.get("passed"),
            "evidence_coverage": evidence["coverage"],
        },
        "client_summary": {
            "raw_input": state.get("raw_input"),
            "ips": state.get("ips") or {},
            "portfolio": portfolio,
        },
        "approval": state.get("approval") or {},
        "risk_metrics": metrics,
        "explanations": state.get("explanations") or [],
        "citations": citations,
        "evidence": evidence,
        "judge": judge,
        "governance": {
            "approval_status": (state.get("approval") or {}).get("status"),
            "judge_retries": state.get("judge_retries") or 0,
            "judge_passed": judge.get("passed"),
            "strict_citation_gate": run_config.get("strict_citation_gate") is True,
            "manual_review_required": bool(warnings),
        },
        "reproducibility": {
            "as_of_date": run_config.get("as_of_date"),
            "config_hash": run_config.get("config_hash"),
            "computation_hash": meta.get("computation_hash"),
            "method": meta.get("method"),
            "n_observations": meta.get("n_observations"),
            "methodology_ref": meta.get("methodology_ref"),
            "trace_id": state.get("trace_id"),
        },
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }
    return {"report": report}
