"""결정론 리스크 엔진 호출 노드 — app.engine.metrics.compute_metrics() 위임.

6자산군 일별 수익률은 run_config["data_source"]에 따라 두 경로 중 하나로 받는다.
  - "dummy": app.engine.returns.load_returns() — 고정 수식 + parquet 캐시(네트워크 불요).
  - "real" (기본값): app.engine.returns.load_real_returns() — yfinance 조회 +
    parquet 캐시(로컬 전용, git 미커밋 — Yahoo Finance 재배포 제약). 발표 전 1회
    실행해 로컬 캐시를 미리 만들어두면 이후 오프라인에서도 동작한다.
  - 그 외 값은 조용히 dummy로 처리하지 않고 즉시 실패한다 — 실데이터 사용 여부는
    리스크 수치의 핵심 전제이므로 오타를 방치하면 설명가능성이 깨진다.
동일 config·동일 데이터 하에서 computation_hash가 항상 동일함을 보장한다.

이 노드는 approval_gate를 통과한 뒤에만 실행되므로 승인 여부를 다시 검사하지 않는다.
"""
from app.engine.metrics import compute_metrics
from app.engine.returns import (
    DEFAULT_N,
    DEFAULT_REAL_N,
    DEFAULT_RF_ANNUAL,
    FX_TICKER,
    REAL_ASSET_TICKERS,
    data_period,
    load_real_returns,
    load_returns,
)
from app.state import RiskState

VALID_DATA_SOURCES = ("real", "dummy")


def var_engine(state: RiskState) -> dict:
    run_config = state.get("run_config") or {}
    as_of_date = run_config.get("as_of_date")
    data_source = run_config.get("data_source", "real")

    if data_source not in VALID_DATA_SOURCES:
        raise ValueError(
            f"지원하지 않는 data_source입니다: {data_source!r} "
            f"(허용값: {VALID_DATA_SOURCES}). 오타가 조용히 dummy로 처리되면 "
            "리포트 수치의 출처가 왜곡되므로 즉시 실패시킵니다."
        )

    if data_source == "real":
        n = run_config.get("var_lookback_days")
        n = n if n is not None else DEFAULT_REAL_N
        rf_rate = run_config.get("rf_rate")
        rf_annual = rf_rate if rf_rate is not None else DEFAULT_RF_ANNUAL
        returns_df = load_real_returns(n=n, as_of_date=as_of_date, rf_annual=rf_annual)
        fx_applied = True  # 해외자산은 USD/KRW 환율변동을 명시적으로 결합했다.
        tickers = dict(REAL_ASSET_TICKERS)
        fx_ticker = FX_TICKER
    else:
        n = run_config.get("var_lookback_days") or DEFAULT_N
        returns_df = load_returns(n=n, as_of_date=as_of_date)
        fx_applied = False  # 더미 단계 — 환율 미적용.
        tickers = None
        fx_ticker = None

    seed = run_config.get("seed")
    seed = seed if seed is not None else 42

    metrics = compute_metrics(
        returns_df=returns_df,
        portfolio=state.get("portfolio", []),
        confidence=run_config.get("var_confidence", 0.99),
        horizons=run_config.get("horizons", [1, 10]),
        base_currency=run_config.get("base_currency", "KRW"),
        data_period_meta=data_period(returns_df),
        fx_applied=fx_applied,
        methodology_ref="methodology_var_cvar_2026",
        data_source=data_source,
        tickers=tickers,
        fx_ticker=fx_ticker,
        seed=seed,  # VaR/CVaR 신뢰구간(bootstrap)의 재현성 고정 — config.yaml의 seed
    )
    return {"metrics": metrics}
