"""결정론 계층 — historical VaR/CVaR 계산.

주의: 이 패키지(app.engine)에서는 langchain/llm 관련 import 금지.
순수 수치 계산만 수행하며, 동일 입력에 대해 항상 동일 출력을 보장한다.
"""
import math

import numpy as np

from app.engine.stress import run_stress
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


def compute_metrics(
    returns: list[float],
    portfolio: list[dict],
    confidence: float = 0.99,
    horizons: list[int] | None = None,
) -> dict:
    """포트폴리오 리스크 지표 일괄 계산.

    - horizon h일 지표는 1일 지표의 sqrt(h) 스케일링.
    - meta.computation_hash: 입력+결과의 sha256 (재현성 검증용).
    """
    horizons = horizons or [1, 10]
    arr = np.asarray(returns, dtype=float)
    if arr.size == 0:
        raise ValueError("수익률 데이터(returns)가 비어 있어 리스크 지표를 계산할 수 없습니다.")
    if not portfolio:
        raise ValueError("포트폴리오 데이터(portfolio)가 비어 있어 리스크 지표를 계산할 수 없습니다.")
    if not (0.0 < confidence < 1.0):
        raise ValueError("신뢰수준(confidence)은 0과 1 사이의 값이어야 합니다.")
    total_value = sum(p["value_krw"] for p in portfolio)

    var_1d = historical_var(arr, confidence)
    cvar_1d = historical_cvar(arr, confidence)

    per_horizon = {}
    for h in horizons:
        scale = math.sqrt(h)
        per_horizon[f"{h}d"] = {
            "var_pct": round(var_1d * scale, 8),
            "cvar_pct": round(cvar_1d * scale, 8),
            "var_krw": round(total_value * var_1d * scale, 2),
            "cvar_krw": round(total_value * cvar_1d * scale, 2),
        }

    stress = run_stress(portfolio)

    payload = {
        "inputs": {
            "returns": [round(float(r), 10) for r in arr],
            "confidence": confidence,
            "horizons": horizons,
            "portfolio": portfolio,
        },
        "results": {"per_horizon": per_horizon, "stress": stress},
    }

    return {
        "confidence": confidence,
        "horizons": per_horizon,
        "stress": stress,
        "meta": {
            "method": "historical",
            "n_observations": int(arr.size),
            "computation_hash": sha256_of_dict(payload),
        },
    }
