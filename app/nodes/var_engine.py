"""결정론 리스크 엔진 호출 노드 — app.engine.metrics.compute_metrics() 위임.

6자산군 일별 수익률은 run_config["data_source"]에 따라 두 경로 중 하나로 받는다.
  - "dummy": app.engine.returns.load_returns() — 고정 수식 + parquet 캐시(네트워크 불요).
  - "real" (기본값): app.engine.returns.load_real_returns() — yfinance 조회 +
    parquet 캐시. 캐시가 이미 존재하면(레포에 커밋된 스냅샷 포함) 네트워크 없이도 동작한다.
동일 config·동일 데이터 하에서 computation_hash가 항상 동일함을 보장한다.

이 노드는 approval_gate를 통과한 뒤에만 실행되므로 승인 여부를 다시 검사하지 않는다.
"""
from app.engine.metrics import compute_metrics
from app.engine.returns import DEFAULT_N, DEFAULT_RF_ANNUAL, data_period, load_real_returns, load_returns
from app.state import RiskState


def var_engine(state: RiskState) -> dict:
    run_config = state.get("run_config") or {}
    n = run_config.get("var_lookback_days") or DEFAULT_N
    as_of_date = run_config.get("as_of_date")
    data_source = run_config.get("data_source", "real")

    if data_source == "real":
        rf_annual = run_config.get("rf_rate") or DEFAULT_RF_ANNUAL
        returns_df = load_real_returns(n=n, as_of_date=as_of_date, rf_annual=rf_annual)
        fx_applied = True  # 해외자산은 USD/KRW 환율변동을 명시적으로 결합했다.
    else:
        returns_df = load_returns(n=n, as_of_date=as_of_date)
        fx_applied = False  # 더미 단계 — 환율 미적용.

    metrics = compute_metrics(
        returns_df=returns_df,
        portfolio=state.get("portfolio", []),
        confidence=run_config.get("var_confidence", 0.99),
        horizons=run_config.get("horizons", [1, 10]),
        base_currency=run_config.get("base_currency", "KRW"),
        data_period_meta=data_period(returns_df),
        fx_applied=fx_applied,
        methodology_ref="methodology_var_cvar_2026",
    )
    return {"metrics": metrics}
