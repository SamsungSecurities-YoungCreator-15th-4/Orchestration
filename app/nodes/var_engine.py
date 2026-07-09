"""결정론 리스크 엔진 호출 노드 — app.engine.metrics.compute_metrics() 위임.

6자산군 일별 수익률은 app.engine.returns.load_returns()에서 받아온다
(현재는 고정 수식 더미 + parquet 캐시, 실데이터 전환 시 그 로더만 교체).
동일 config·동일 데이터 하에서 computation_hash가 항상 동일함을 보장한다.

이 노드는 approval_gate를 통과한 뒤에만 실행되므로 승인 여부를 다시 검사하지 않는다.
"""
from app.engine.metrics import compute_metrics
from app.engine.returns import DEFAULT_N, data_period, load_returns
from app.state import RiskState


def var_engine(state: RiskState) -> dict:
    run_config = state.get("run_config") or {}

    returns_df = load_returns(
        n=run_config.get("var_lookback_days", DEFAULT_N),
        as_of_date=run_config.get("as_of_date"),
    )

    metrics = compute_metrics(
        returns_df=returns_df,
        portfolio=state.get("portfolio", []),
        confidence=run_config.get("var_confidence", 0.99),
        horizons=run_config.get("horizons", [1, 10]),
        base_currency=run_config.get("base_currency", "KRW"),
        data_period_meta=data_period(returns_df),
        fx_applied=False,  # 더미 단계 — 환율 미적용. 실데이터 전환 시 True/규약 반영.
        methodology_ref="methodology_var_cvar_2026",
    )
    return {"metrics": metrics}
