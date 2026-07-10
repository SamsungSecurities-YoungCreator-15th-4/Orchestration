"""결정론 계층 — historical VaR/CVaR 계산.

주의: 이 패키지(app.engine)에서는 langchain/llm 관련 import 금지.
순수 수치 계산만 수행하며, 동일 입력에 대해 항상 동일 출력을 보장한다.
"""
import math

import numpy as np
import pandas as pd

from app.engine.stress import run_all_stress
from app.utils.hashing import sha256_of_dict


def historical_var(returns: np.ndarray, confidence: float = 0.99) -> float:
    """Historical VaR: 수익률 분포의 (1-confidence) 분위수 손실 (양수 = 손실)."""
    q = np.quantile(np.asarray(returns, dtype=float), 1.0 - confidence)
    return float(-q)


def historical_cvar(returns: np.ndarray, confidence: float = 0.99) -> float:
    """Historical CVaR(Expected Shortfall): VaR 초과 손실의 평균 (양수 = 손실)."""
    arr = np.asarray(returns, dtype=float)
    q = np.quantile(arr, 1.0 - confidence)
    tail = arr[arr <= q]
    return float(-tail.mean())


def portfolio_returns(returns_df: pd.DataFrame, portfolio: list[dict]) -> np.ndarray:
    """자산군별 일별 수익률 × 금액 비중으로 포트폴리오 일별 수익률 시계열 합성.

    비중은 포트폴리오 금액(value_krw)에서 결정론적으로 산출한다.
    같은 자산군이 여러 종목으로 들어오면 합산한다.
    """
    total_value = sum(p["value_krw"] for p in portfolio)
    if not total_value:
        raise ValueError("포트폴리오 총액이 0이라 비중을 계산할 수 없습니다.")
    weights: dict[str, float] = {}
    for p in portfolio:
        ac = p["asset_class"]
        if ac not in returns_df.columns:
            # 수익률 데이터에 없는 자산군은 비중이 조용히 누락되어 리스크가
            # 과소평가되므로 명시적으로 실패시킨다.
            raise ValueError(f"수익률 데이터에 존재하지 않는 자산군입니다: {ac}")
        weights[ac] = weights.get(ac, 0.0) + p["value_krw"] / total_value
    w = np.array([weights.get(c, 0.0) for c in returns_df.columns], dtype=float)
    return returns_df.to_numpy(dtype=float) @ w


def compute_metrics(
    returns_df: pd.DataFrame,
    portfolio: list[dict],
    confidence: float = 0.99,
    horizons: list[int] | None = None,
    base_currency: str = "KRW",
    data_period_meta: dict | None = None,
    fx_applied: bool = False,
    methodology_ref: str | None = None,
) -> dict:
    """포트폴리오 리스크 지표 일괄 계산.

    - returns_df: 6자산군 일별 수익률(app.engine.returns.load_returns 결과).
    - horizon h일 지표는 1일 지표의 sqrt(h) 스케일링(√t rule).
    - meta.computation_hash: 입력+결과의 sha256 (재현성 검증용).

    §8 TBD 메모(실데이터 전환 시):
    - fx_applied/base_currency는 현재 계산에 관여하지 않는 통과 메타데이터다.
      실데이터 전환 시 fx_applied는 인자가 아니라 returns_df(로더)가 스스로
      답해야 하는 속성으로 승격한다(예: returns.py의 fx_meta(df)로 내려받기).
    - data_period는 computation_hash payload에 포함해, '같은 수익률 값·다른
      기간'이 해시로 구분되도록 한다(아래 payload 참조).
    """
    horizons = horizons or [1, 10]
    if returns_df is None or len(returns_df) == 0:
        raise ValueError("수익률 데이터(returns_df)가 비어 있어 리스크 지표를 계산할 수 없습니다.")
    if not portfolio:
        raise ValueError("포트폴리오 데이터(portfolio)가 비어 있어 리스크 지표를 계산할 수 없습니다.")
    if not (0.0 < confidence < 1.0):
        raise ValueError("신뢰수준(confidence)은 0과 1 사이의 값이어야 합니다.")

    total_value = sum(p["value_krw"] for p in portfolio)
    port_ret = portfolio_returns(returns_df, portfolio)

    var_1d = historical_var(port_ret, confidence)
    cvar_1d = historical_cvar(port_ret, confidence)

    per_horizon = {}
    for h in horizons:
        scale = math.sqrt(h)
        per_horizon[f"{h}d"] = {
            "var_pct": round(var_1d * scale, 8),
            "cvar_pct": round(cvar_1d * scale, 8),
            "var_krw": round(total_value * var_1d * scale, 2),
            "cvar_krw": round(total_value * cvar_1d * scale, 2),
        }

    stress = run_all_stress(portfolio)

    payload = {
        "inputs": {
            "asset_returns": {
                c: [round(float(x), 10) for x in returns_df[c].to_numpy()]
                for c in returns_df.columns
            },
            "confidence": confidence,
            "horizons": horizons,
            "portfolio": portfolio,
            "base_currency": base_currency,
            "fx_applied": fx_applied,
            "data_period": data_period_meta,
            "methodology_ref": methodology_ref,
        },
        "results": {"per_horizon": per_horizon, "stress": stress},
    }

    return {
        "confidence": confidence,
        "horizons": per_horizon,
        "stress": stress,
        "meta": {
            "method": "historical",
            "scaling": "sqrt_t",
            "n_observations": int(len(returns_df)),
            "base_currency": base_currency,
            "data_period": data_period_meta,
            "fx_applied": fx_applied,
            "methodology_ref": methodology_ref,
            "computation_hash": sha256_of_dict(payload),
        },
    }
