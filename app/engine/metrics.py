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


def _asset_weights(returns_df: pd.DataFrame, portfolio: list[dict]) -> dict[str, float]:
    """포트폴리오 금액(value_krw)에서 자산군별 비중을 결정론적으로 산출한다.

    같은 자산군이 여러 종목으로 들어오면 합산한다. portfolio_returns와
    tail_contribution이 동일한 비중 계산을 공유하도록 분리했다(중복 방지).
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
    return weights


def portfolio_returns(returns_df: pd.DataFrame, portfolio: list[dict]) -> np.ndarray:
    """자산군별 일별 수익률 × 금액 비중으로 포트폴리오 일별 수익률 시계열 합성."""
    weights = _asset_weights(returns_df, portfolio)
    w = np.array([weights.get(c, 0.0) for c in returns_df.columns], dtype=float)
    return returns_df.to_numpy(dtype=float) @ w


def tail_contribution(
    returns_df: pd.DataFrame, portfolio: list[dict], confidence: float = 0.99
) -> dict:
    """CVaR 꼬리 구간(손실이 VaR을 초과하는 날들)에서 자산군별 평균 손실 기여도.

    "포트폴리오가 X원 위험하다"에서 "그중 얼마가 어느 자산군에서 오는가"로
    드릴다운하기 위한 지표(R6 평가기준의 "드릴다운" 항목). 꼬리 구간 날짜들에
    대해 자산군별 (수익률 × 비중 × 총액)의 평균을 내면, 그 합은 정확히
    포트폴리오 CVaR(원화)과 같다 — 가중합의 평균은 평균의 가중합이기 때문에
    분해가 수학적으로 정확히 맞아떨어진다(근사가 아님).
    """
    total_value = sum(p["value_krw"] for p in portfolio)
    weights = _asset_weights(returns_df, portfolio)
    port_ret = portfolio_returns(returns_df, portfolio)

    q = np.quantile(port_ret, 1.0 - confidence)
    tail_mask = port_ret <= q
    if not tail_mask.any():
        # 관측치가 극히 적어 꼬리가 비는 극단적 경우 방어 — 전부 0으로 반환.
        return {c: 0.0 for c in returns_df.columns}

    contributions: dict[str, float] = {}
    for c in returns_df.columns:
        w = weights.get(c, 0.0)
        avg_asset_return = float(returns_df.loc[tail_mask, c].mean())
        contributions[c] = round(-avg_asset_return * w * total_value, 2)
    return contributions


def var_backtest(port_ret: np.ndarray, var_1d: float, confidence: float = 0.99) -> dict:
    """1일 VaR 위반율(violation rate) 표본 내(in-sample) 점검.

    실제 일별 손실이 VaR 추정치를 초과한 날의 비율을 세어, 이론적 기대치
    (1-confidence, 99% VaR이면 약 1%)와 비교한다. 값이 크게 벗어나면 모델이
    분포를 잘못 잡았다는 신호다.

    주의(정직한 한계): VaR 자체가 이 표본의 (1-confidence) 분위수로 정의되므로,
    같은 표본에서 위반율을 재면 이론상 거의 정확히 1-confidence에 가깝게
    나오는 것이 당연하다(계산이 맞는지 확인하는 항등식에 가깝다). 진짜 예측력
    검증(모델이 미래에도 맞는지)은 별도의 표본 밖(out-of-sample) 백테스트가
    필요하며, 여기서는 하지 않는다 — 위조정밀도를 피하기 위해 그 한계를
    결과에 note로 명시한다.
    """
    port_ret = np.asarray(port_ret, dtype=float)
    losses = -port_ret  # 양수 = 손실
    n = int(len(losses))
    violations = int((losses > var_1d).sum())
    expected_rate = round(1.0 - confidence, 6)
    violation_rate = round(violations / n, 6) if n else 0.0
    return {
        "n_observations": n,
        "violations": violations,
        "violation_rate": violation_rate,
        "expected_rate": expected_rate,
        "note": (
            "표본 내(in-sample) 점검입니다 — VaR이 이 표본의 분위수로 정의되므로 "
            "위반율이 기대치에 가까운 것은 계산 정합성 확인에 가깝고, 모델의 "
            "미래 예측력을 검증하는 표본 밖(out-of-sample) 백테스트가 아닙니다."
        ),
    }


def lag1_autocorrelation(port_ret: np.ndarray) -> float:
    """포트폴리오 일별 수익률의 1차 자기상관계수.

    methodology_var_cvar_2026 §4가 텍스트로만 적어둔 한계("자기상관이 강한
    구간에서는 √t 스케일링이 부정확할 수 있다")를 실제 숫자로 정량화한다.
    0에 가까울수록 √t 스케일링의 시계열 독립성 가정이 잘 맞는다는 뜻이다.
    """
    port_ret = np.asarray(port_ret, dtype=float)
    if len(port_ret) < 3:
        return 0.0
    corr = np.corrcoef(port_ret[:-1], port_ret[1:])[0, 1]
    return 0.0 if np.isnan(corr) else round(float(corr), 6)


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

    tail_contribution_krw = tail_contribution(returns_df, portfolio, confidence)
    cvar_1d_krw = per_horizon["1d"]["cvar_krw"] if "1d" in per_horizon else round(total_value * cvar_1d, 2)
    tail_contribution_pct = {
        c: round(v / cvar_1d_krw, 6) if cvar_1d_krw else 0.0
        for c, v in tail_contribution_krw.items()
    }
    backtest = var_backtest(port_ret, var_1d, confidence)
    autocorrelation_lag1 = lag1_autocorrelation(port_ret)

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
        "results": {
            "per_horizon": per_horizon,
            "stress": stress,
            "tail_contribution_krw": tail_contribution_krw,
            "backtest": backtest,
            "autocorrelation_lag1": autocorrelation_lag1,
        },
    }

    return {
        "confidence": confidence,
        "horizons": per_horizon,
        "stress": stress,
        "drilldown": {
            "tail_contribution_krw": tail_contribution_krw,
            "tail_contribution_pct": tail_contribution_pct,
        },
        "backtest": backtest,
        "meta": {
            "method": "historical",
            "scaling": "sqrt_t",
            "n_observations": int(len(returns_df)),
            "base_currency": base_currency,
            "data_period": data_period_meta,
            "fx_applied": fx_applied,
            "autocorrelation_lag1": autocorrelation_lag1,
            "methodology_ref": methodology_ref,
            "computation_hash": sha256_of_dict(payload),
        },
    }
