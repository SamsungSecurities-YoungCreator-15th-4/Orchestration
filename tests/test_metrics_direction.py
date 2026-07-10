"""VaR/CVaR/스트레스 방향성 + 재현성 검증.

초안 합격선: 정확한 수치값이 아니라 '방향(단조성)'과 '재현성'을 검증한다.
"""
import functools
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.metrics import (
    compute_metrics,
    historical_cvar,
    historical_var,
    portfolio_returns,
)
from app.engine import returns as returns_mod
from app.engine.returns import ASSET_CLASSES, _generate_dummy_returns, load_real_returns, load_returns
from app.engine.stress import run_all_stress
from app.nodes.var_engine import var_engine

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


def test_stress_loss_sign_is_positive():
    """스트레스 loss_krw는 양수=손실 규약(historical_var와 통일)이다."""
    res = run_all_stress(PORTFOLIO)
    assert res["A_high_rate"]["loss_krw"] > 0
    assert res["B_strong_usd"]["loss_krw"] > 0


def test_stress_loss_ge_normal_var():
    """스트레스 손실 ≥ 평상시(정상 시장) 1일 VaR. (양수=손실이라 abs 불필요)"""
    m = _metrics()
    var_1d_krw = m["horizons"]["1d"]["var_krw"]
    for name, res in m["stress"].items():
        assert res["loss_krw"] >= var_1d_krw, name


def test_stress_A_worse_than_B():
    """고금리(A)는 전 자산 동반 하락이라 강달러(B, FX 상쇄)보다 손실이 크다."""
    res = run_all_stress(PORTFOLIO)
    assert res["A_high_rate"]["loss_krw"] > res["B_strong_usd"]["loss_krw"]


# --- 리뷰 반영: 자산군 방어 (metrics + stress 대칭) ---
def test_portfolio_returns_rejects_unknown_asset():
    """수익률 데이터에 없는 자산군은 비중 누락 대신 명시적으로 실패한다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    with pytest.raises(ValueError):
        portfolio_returns(df, [{"asset_class": "crypto", "value_krw": 100}])


def test_stress_rejects_unknown_asset():
    """시나리오에 충격이 정의되지 않은 자산군은 조용히 0이 아니라 실패한다."""
    with pytest.raises(ValueError):
        run_all_stress([{"asset_class": "crypto", "value_krw": 100}])


# --- 리뷰 반영: 캐시 무효화(낡은 캐시 미반환) & 비영업일 히트 ---
def test_stale_cache_not_returned(tmp_path):
    """낡은 캐시가 있어도 파라미터가 다르면 반환하지 않고 재생성한다."""
    path = tmp_path / "returns.parquet"
    load_returns(as_of_date="2026-06-01", cache_path=path)  # 낡은 캐시 심기
    fresh = load_returns(as_of_date="2026-07-03", cache_path=path)
    assert fresh.index.max().date() == date(2026, 7, 3)


def test_cache_hit_returns_same_data(tmp_path):
    """캐시 히트 경로도 동일 데이터를 반환한다."""
    path = tmp_path / "returns.parquet"
    first = load_returns(as_of_date="2026-07-03", cache_path=path)
    second = load_returns(as_of_date="2026-07-03", cache_path=path)
    pd.testing.assert_frame_equal(first, second, check_freq=False)


def test_cache_hits_on_non_business_day(tmp_path):
    """비영업일(토) as_of_date에서도 캐시가 히트해 재기록하지 않는다."""
    path = tmp_path / "returns.parquet"
    load_returns(as_of_date="2026-07-04", cache_path=path)  # 토요일 → 직전 영업일 정규화
    mtime1 = path.stat().st_mtime_ns
    load_returns(as_of_date="2026-07-04", cache_path=path)
    assert path.stat().st_mtime_ns == mtime1  # 두 번째 호출이 재기록하지 않음


# --- 재현성 ---
def test_reproducibility_identical():
    """같은 입력 2회 실행 → 완전히 동일한 결과(해시 포함)."""
    m1 = _metrics()
    m2 = _metrics()
    assert m1 == m2
    assert m1["meta"]["computation_hash"] == m2["meta"]["computation_hash"]


# --- 리뷰 반영: 관측기간(config)·출처 메타 ---
def test_methodology_ref_in_meta():
    """meta.methodology_ref로 리포트 수치가 방법론 문서와 연결된다."""
    m = compute_metrics(
        _generate_dummy_returns(n=250, as_of_date="2026-07-03"),
        PORTFOLIO,
        methodology_ref="methodology_var_cvar_2026",
    )
    assert m["meta"]["methodology_ref"] == "methodology_var_cvar_2026"


def _load_returns_with_tmp_cache(tmp_path):
    """load_returns의 cache_path 기본값을 tmp 경로로 고정한 래퍼.

    cache_path 기본값은 함수 정의 시점에 CACHE_PATH로 바인딩되므로
    app.engine.returns.CACHE_PATH를 몽키패치해도 반영되지 않는다.
    var_engine이 참조하는 load_returns 자체를 이 래퍼로 교체해야
    테스트가 레포의 실제 data/returns_dummy.parquet 캐시를 건드리지 않는다.
    """
    return functools.partial(returns_mod.load_returns, cache_path=tmp_path / "cache.parquet")


def test_var_engine_respects_lookback_config(tmp_path, monkeypatch):
    """var_lookback_days가 config에서 오면 실제 관측 개수(n_observations)에 반영된다.

    data_source="dummy"로 고정 — 기본값(real)은 committed 캐시와 파라미터가
    맞을 때만 오프라인으로 동작하므로, lookback을 임의로 바꾸는 이 테스트는
    네트워크 의존 없는 dummy 경로로 검증한다.
    """
    monkeypatch.setattr(
        "app.nodes.var_engine.load_returns", _load_returns_with_tmp_cache(tmp_path)
    )
    state = {
        "run_config": {
            "as_of_date": "2026-07-03",
            "var_lookback_days": 120,
            "data_source": "dummy",
        },
        "portfolio": PORTFOLIO,
    }
    result = var_engine(state)
    assert result["metrics"]["meta"]["n_observations"] == 120
    assert result["metrics"]["meta"]["data_period"]["n_observations"] == 120


def test_var_engine_defaults_lookback_when_unset(tmp_path, monkeypatch):
    """var_lookback_days가 config에 없으면 returns.py의 DEFAULT_N(250)을 쓴다(dummy 경로)."""
    monkeypatch.setattr(
        "app.nodes.var_engine.load_returns", _load_returns_with_tmp_cache(tmp_path)
    )
    state = {
        "run_config": {"as_of_date": "2026-07-03", "data_source": "dummy"},
        "portfolio": PORTFOLIO,
    }
    result = var_engine(state)
    assert result["metrics"]["meta"]["n_observations"] == 250


def test_var_engine_defaults_lookback_when_explicitly_none(tmp_path, monkeypatch):
    """var_lookback_days가 명시적으로 None이어도(.get 기본값이 안 먹는 경우) DEFAULT_N을 쓴다(dummy 경로)."""
    monkeypatch.setattr(
        "app.nodes.var_engine.load_returns", _load_returns_with_tmp_cache(tmp_path)
    )
    state = {
        "run_config": {
            "as_of_date": "2026-07-03",
            "var_lookback_days": None,
            "data_source": "dummy",
        },
        "portfolio": PORTFOLIO,
    }
    result = var_engine(state)
    assert result["metrics"]["meta"]["n_observations"] == 250


# --- 실데이터 경로 (yfinance + parquet 캐시) ---
def test_load_real_returns_uses_committed_cache():
    """레포에 커밋된 실데이터 캐시(data/returns_real.parquet)를 읽는다 — 네트워크 불요.

    R7 요구사항(현장 시연은 인터넷 없이도 동작해야 함)의 핵심 전제를 검증한다.
    """
    df = load_real_returns(n=250, as_of_date="2026-07-03")
    assert df.shape == (250, len(ASSET_CLASSES))
    assert list(df.columns) == ASSET_CLASSES
    assert not df.isna().any().any()


def test_real_cache_invalidated_on_param_mismatch(tmp_path, monkeypatch):
    """캐시 meta와 요청 파라미터가 다르면 재수집 경로를 탄다.

    _fetch_real_returns를 스텁으로 바꿔 네트워크 없이 캐시 히트/미스 로직만 검증한다.
    """
    calls = []

    def fake_fetch(n, as_of_date, rf_annual):
        calls.append(n)
        idx = pd.bdate_range(end=pd.Timestamp(as_of_date), periods=n)
        return pd.DataFrame({c: 0.001 for c in ASSET_CLASSES}, index=idx)[ASSET_CLASSES]

    monkeypatch.setattr(returns_mod, "_fetch_real_returns", fake_fetch)
    cache_path = tmp_path / "real.parquet"
    meta_path = tmp_path / "real.meta.json"

    load_real_returns(n=5, as_of_date="2026-07-03", cache_path=cache_path, meta_path=meta_path)
    load_real_returns(n=5, as_of_date="2026-07-03", cache_path=cache_path, meta_path=meta_path)
    assert calls == [5]  # 두 번째 호출은 캐시 히트 — 재수집 없음

    load_real_returns(n=6, as_of_date="2026-07-03", cache_path=cache_path, meta_path=meta_path)
    assert calls == [5, 6]  # n이 달라졌으니 재수집


def test_extract_close_handles_multiindex_columns():
    """yf.download()가 (Close, ticker) MultiIndex 컬럼을 줄 때 정상 추출된다."""
    idx = pd.bdate_range("2026-01-01", periods=3)
    cols = pd.MultiIndex.from_product([["Close", "Open"], ["^KS11"]])
    data = pd.DataFrame([[1.0, 1.1], [2.0, 2.1], [3.0, 3.1]], index=idx, columns=cols)
    out = returns_mod._extract_close(data, "^KS11")
    assert list(out) == [1.0, 2.0, 3.0]


def test_extract_close_handles_flat_columns():
    """yf.download()가 flat 컬럼(Close 단일 Series)을 줄 때도 KeyError 없이 추출된다."""
    idx = pd.bdate_range("2026-01-01", periods=3)
    data = pd.DataFrame({"Close": [1.0, 2.0, 3.0], "Open": [1.1, 2.1, 3.1]}, index=idx)
    out = returns_mod._extract_close(data, "^KS11")
    assert list(out) == [1.0, 2.0, 3.0]


def test_apply_fx_conversion_formula():
    """r_KRW = (1+r_USD)*(1+r_FX) - 1 공식이 정확히 계산되는지 직접 검증(네트워크 불요).

    이전에는 이 산식이 커밋된 실데이터 캐시(블랙박스)나 _fetch_real_returns를
    통째로 스텁으로 바꾼 캐시 테스트로만 간접 검증됐고, 공식 자체를 검증하는
    테스트가 없었다. _apply_fx_conversion을 순수 함수로 분리해 직접 확인한다.
    """
    idx = pd.bdate_range("2026-01-01", periods=2)
    # 현지통화(USD) 수익률: global_equity/global_bond/alternatives = +2%, 나머지 = +1%
    pct = pd.DataFrame(
        {ac: [0.02, 0.02] if ac in returns_mod.USD_DENOMINATED else [0.01, 0.01]
         for ac in returns_mod.REAL_ASSET_TICKERS},
        index=idx,
    )
    fx_ret = pd.Series([0.03, -0.01], index=idx)  # USD/KRW 변동률(1일차 원화약세, 2일차 원화강세)

    out = returns_mod._apply_fx_conversion(pct, fx_ret, rf_annual=0.0325)

    # 원화 상장 자산(domestic_*)은 환율 무관 — 현지통화 수익률 그대로.
    assert out["domestic_equity"].iloc[0] == pytest.approx(0.01)
    assert out["domestic_bond"].iloc[1] == pytest.approx(0.01)
    # USD 상장 자산은 (1+r_USD)*(1+r_FX)-1 로 결합.
    expected_day1 = (1 + 0.02) * (1 + 0.03) - 1
    expected_day2 = (1 + 0.02) * (1 - 0.01) - 1
    assert out["global_equity"].iloc[0] == pytest.approx(expected_day1)
    assert out["global_equity"].iloc[1] == pytest.approx(expected_day2)
    assert out["alternatives"].iloc[0] == pytest.approx(expected_day1)
    # cash는 시장데이터 무관 — rf_annual/252 상수.
    assert out["cash"].tolist() == pytest.approx([0.0325 / 252, 0.0325 / 252])


def test_var_engine_uses_real_data_by_default():
    """data_source 미지정 시 기본값 real — committed 캐시로 오프라인 동작, fx_applied=True."""
    state = {
        "run_config": {"as_of_date": "2026-07-03", "var_lookback_days": 250},
        "portfolio": PORTFOLIO,
    }
    result = var_engine(state)
    meta = result["metrics"]["meta"]
    assert meta["fx_applied"] is True
    assert meta["n_observations"] == 250
    assert meta["methodology_ref"] == "methodology_var_cvar_2026"


def test_var_engine_dummy_source_explicit_opt_in(tmp_path, monkeypatch):
    """data_source="dummy"를 명시하면 fx_applied=False로 더미 경로를 탄다."""
    monkeypatch.setattr(
        "app.nodes.var_engine.load_returns", _load_returns_with_tmp_cache(tmp_path)
    )
    state = {
        "run_config": {"as_of_date": "2026-07-03", "data_source": "dummy"},
        "portfolio": PORTFOLIO,
    }
    result = var_engine(state)
    assert result["metrics"]["meta"]["fx_applied"] is False
