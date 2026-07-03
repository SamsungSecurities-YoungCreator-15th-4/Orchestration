"""최종 리포트 조립 — 전체 state 요약 + 면책 문구 + trace_id."""
from app.state import RiskState

DISCLAIMER = (
    "본 리포트는 내부 리스크 점검 목적으로 자동 생성된 자료이며, "
    "투자 권유 또는 수익 보장을 의미하지 않습니다. 모든 수치는 "
    "과거 데이터 기반 추정치로 실제 결과와 다를 수 있습니다."
)


def assemble_report(state: RiskState) -> dict:
    metrics = state.get("metrics", {})
    report = {
        "title": "재현가능·설명가능 리스크 리포트 (스켈레톤)",
        "as_of_date": state.get("run_config", {}).get("as_of_date"),
        "trace_id": state.get("trace_id"),
        "client_summary": {
            "raw_input": state.get("raw_input"),
            "ips": state.get("ips", {}),
        },
        "approval": state.get("approval", {}),
        "risk_metrics": metrics,
        "explanations": state.get("explanations", []),
        "citations": state.get("citations", []),
        "judge": state.get("judge", {}),
        "reproducibility": {
            "config_hash": state.get("run_config", {}).get("config_hash"),
            "computation_hash": metrics.get("meta", {}).get("computation_hash"),
        },
        "disclaimer": DISCLAIMER,
    }
    return {"report": report}
