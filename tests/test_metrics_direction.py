"""VaR/CVaR/스트레스 방향성 + 재현성 검증.

초안 합격선: 정확한 수치값이 아니라 '방향(단조성)'과 '재현성'을 검증한다.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tempfile

import pytest

from app.engine.metrics import (
    compute_metrics,
    historical_cvar,
    historical_var,
    portfolio_returns,
)
from app.engine.returns import _generate_dummy_returns, load_returns
from app.engine.stress import run_all_stress

# 6자산군 더미 포트폴리오(총 50억) — load_inputs.py와 동일 구조.
PORTFOLIO = [
    {"asset_class": "domestic_equity", "value_krw": 1_250_000_000},
    {"asset_class": "global_equity", "value_krw": 1_000_000_000},
    {"asset_class": "domestic_bond", "value_krw": 1_250_000_000},
    {"asset_class": "global_bond", "value_krw": 750_000_000},
    {"asset_class": "alternatives", "value_krw": 500_000_000},
    {"asset_class": "cash", "value_krw": 250_000_000},
]


def _wave_returns(scale: float, n: int = 250) -> np.ndarray:
    """고정 수식 기반 더미 수익률 (결정론적, 랜덤 미사용)."""
    i = np.arange(n, dtype=float)
    return scale * np.sin(0.9 * i) + 0.3 * scale * np.cos(0.35 * i)


def _metrics():
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    return compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10])


# --- VaR/CVaR 기본 성질 ---
def test_var_direction_high_vol_greater():
    """변동성이 큰 입력의 VaR이 더 크다."""
    low_vol = _wave_returns(scale=0.005)
    high_vol = _wave_returns(scale=0.02)
    assert historical_var(high_vol, 0.99) > historical_var(low_vol, 0.99)


def test_cvar_gte_var():
    """동일 신뢰수준에서 CVaR ≥ VaR."""
    returns = _wave_returns(scale=0.012)
    assert historical_cvar(returns, 0.99) >= historical_var(returns, 0.99)


def test_cvar_gte_var_portfolio():
    """포트폴리오 산출 결과에서도 CVaR ≥ VaR."""
    m = _metrics()
    assert m["horizons"]["1d"]["cvar_pct"] >= m["horizons"]["1d"]["var_pct"]


def test_10d_var_gte_1d_var():
    """√t 스케일링으로 10일 VaR ≥ 1일 VaR."""
    m = _metrics()
    assert m["horizons"]["10d"]["var_pct"] >= m["horizons"]["1d"]["var_pct"]
    assert m["horizons"]["10d"]["var_krw"] >= m["horizons"]["1d"]["var_krw"]


# --- 스트레스 방향성 ---
def test_stress_two_scenarios_present():
    """스트레스는 A(고금리)·B(강달러) 2종을 나란히 산출한다."""
    res = run_all_stress(PORTFOLIO)
    assert set(res.keys()) == {"A_high_rate", "B_strong_usd"}


def test_stress_loss_ge_normal_var():
    """스트레스 손실 ≥ 평상시(정상 시장) 1일 VaR."""
    m = _metrics()
    var_1d_krw = m["horizons"]["1d"]["var_krw"]
    for name, res in m["stress"].items():
        # 손실은 음수로 저장되므로 절대값으로 비교.
        assert abs(res["loss_krw"]) >= var_1d_krw, name


def test_stress_A_worse_than_B():
    """고금리(A)는 전 자산 동반 하락이라 강달러(B, FX 상쇄)보다 손실이 크다."""
    res = run_all_stress(PORTFOLIO)
    assert abs(res["A_high_rate"]["loss_krw"]) > abs(res["B_strong_usd"]["loss_krw"])


# --- 리뷰 반영: 캐시 무효화 & 자산군 방어 ---
def test_cache_invalidated_on_param_change():
    """캐시가 있어도 as_of_date가 다르면 재생성한다(낡은 캐시 반환 금지)."""
    path = tempfile.mktemp(suffix=".parquet")
    a = load_returns(as_of_date="2026-07-03", cache_path=path)
    b = load_returns(as_of_date="2026-06-01", cache_path=path)
    assert a.index.max() != b.index.max()


def test_portfolio_returns_rejects_unknown_asset():
    """수익률 데이터에 없는 자산군은 비중 누락 대신 명시적으로 실패한다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    with pytest.raises(ValueError):
        portfolio_returns(df, [{"asset_class": "crypto", "value_krw": 100}])


# --- 재현성 ---
def test_reproducibility_identical():
    """같은 입력 2회 실행 → 완전히 동일한 결과(해시 포함)."""
    m1 = _metrics()
    m2 = _metrics()
    assert m1 == m2
    assert m1["meta"]["computation_hash"] == m2["meta"]["computation_hash"]
