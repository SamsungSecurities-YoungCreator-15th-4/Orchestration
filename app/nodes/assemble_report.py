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


def _risk_summary(metrics: dict) -> dict:
    horizons = metrics.get("horizons") or {}
    stress = metrics.get("stress") or {}
    return {
        "confidence": metrics.get("confidence"),
        "var_1d_krw": (horizons.get("1d") or {}).get("var_krw"),
        "cvar_1d_krw": (horizons.get("1d") or {}).get("cvar_krw"),
        "var_10d_krw": (horizons.get("10d") or {}).get("var_krw"),
        "cvar_10d_krw": (horizons.get("10d") or {}).get("cvar_krw"),
        "stress_scenario": stress.get("scenario"),
        "stress_loss_krw": stress.get("loss_krw"),
        "stress_loss_pct": stress.get("loss_pct"),
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
            "manual_review_required": bool(warnings),
        },
        "reproducibility": {
            "as_of_date": run_config.get("as_of_date"),
            "config_hash": run_config.get("config_hash"),
            "computation_hash": meta.get("computation_hash"),
            "method": meta.get("method"),
            "n_observations": meta.get("n_observations"),
            "trace_id": state.get("trace_id"),
        },
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }
    return {"report": report}
