"""VaR/CVaR/스트레스 방향성 + 재현성 검증.

초안 합격선: 정확한 수치값이 아니라 '방향(단조성)'과 '재현성'을 검증한다.
"""
import functools
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.metrics import (
    bootstrap_var_cvar_ci,
    compute_metrics,
    historical_cvar,
    historical_var,
    lag1_autocorrelation,
    portfolio_returns,
    tail_contribution,
    var_backtest,
    var_ci_order_statistic,
)
from app.engine import returns as returns_mod
from app.engine.returns import ASSET_CLASSES, _generate_dummy_returns, load_real_returns, load_returns
from app.engine.stress import run_all_stress, SCENARIO_A_HIGH_RATE, SCENARIO_B_STRONG_USD, SCENARIO_C_COVID
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
def test_stress_three_scenarios_present():
    """스트레스는 A(고금리)·B(강달러)·C(코로나) 3종을 나란히 산출한다."""
    res = run_all_stress(PORTFOLIO)
    assert set(res.keys()) == {"A_high_rate", "B_strong_usd", "C_covid"}


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


# --- 자산군 기여도 드릴다운 (R6 "드릴다운" 항목) ---
def test_tail_contribution_sums_to_cvar_krw():
    """자산군별 꼬리 기여도(KRW)의 합은 정확히 포트폴리오 1일 CVaR(KRW)과 같다.

    가중합의 평균은 평균의 가중합이므로 근사가 아니라 수학적으로 정확히
    맞아떨어져야 한다(반올림 오차 수준만 허용).
    """
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    contrib = tail_contribution(df, PORTFOLIO, confidence=0.99)
    m = compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10])
    assert sum(contrib.values()) == pytest.approx(m["horizons"]["1d"]["cvar_krw"], abs=1.0)


def test_tail_contribution_direction_high_vol_asset_contributes_more():
    """변동성이 큰 자산군일수록(동일 비중이면) 꼬리 손실 기여도가 커야 한다."""
    idx = pd.bdate_range(end="2026-07-03", periods=250)
    df = pd.DataFrame(
        {
            "domestic_equity": _wave_returns(scale=0.02),  # 고변동
            "global_equity": _wave_returns(scale=0.005),  # 저변동
            "domestic_bond": _wave_returns(scale=0.005),
            "global_bond": _wave_returns(scale=0.005),
            "alternatives": _wave_returns(scale=0.005),
            "cash": _wave_returns(scale=0.0001),
        },
        index=idx,
    )
    equal_portfolio = [
        {"asset_class": c, "value_krw": 1_000_000_000} for c in df.columns
    ]
    contrib = tail_contribution(df, equal_portfolio, confidence=0.99)
    assert contrib["domestic_equity"] > contrib["global_equity"]


def test_drilldown_present_in_compute_metrics_output():
    """compute_metrics 반환값에 drilldown(krw/pct)이 포함된다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    m = compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10])
    assert set(m["drilldown"]["tail_contribution_krw"]) == set(df.columns)
    assert set(m["drilldown"]["tail_contribution_pct"]) == set(df.columns)


def test_computation_hash_payload_covers_full_drilldown(monkeypatch):
    """반환되는 원화·비율 기여도 전체가 computation_hash 입력에 포함된다."""
    captured = {}

    def capture_hash(payload):
        captured["payload"] = payload
        return "captured-hash"

    monkeypatch.setattr("app.engine.metrics.sha256_of_dict", capture_hash)
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")

    metrics = compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10])

    assert metrics["meta"]["computation_hash"] == "captured-hash"
    assert captured["payload"]["results"]["drilldown"] == metrics["drilldown"]


# --- VaR 백테스트(표본 내 위반율 점검) ---
def test_var_backtest_violation_rate_near_expected():
    """위반율이 이론적 기대치(1-confidence) 근처에 있어야 한다(표본 내 점검)."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    var_1d = historical_var(port_ret, 0.99)
    bt = var_backtest(port_ret, var_1d, confidence=0.99)
    assert bt["n_observations"] == 250
    assert bt["expected_rate"] == pytest.approx(0.01)
    # 표본 내 정의상 위반율은 기대치에서 크게 벗어나지 않아야 한다(1~2건 오차 허용).
    assert abs(bt["violation_rate"] - bt["expected_rate"]) < 0.02


def test_var_backtest_zero_violations_when_var_is_max_loss():
    """VaR을 표본 최대 손실보다 크게 잡으면 위반이 0건이어야 한다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    huge_var = float(-port_ret.min()) + 1.0  # 표본 내 어떤 손실보다도 큰 VaR
    bt = var_backtest(port_ret, huge_var, confidence=0.99)
    assert bt["violations"] == 0
    assert bt["violation_rate"] == 0.0


def test_backtest_present_in_compute_metrics_output():
    """compute_metrics 반환값에 backtest가 포함되고 note로 in-sample 한계를 명시한다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    m = compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10])
    assert "violation_rate" in m["backtest"]
    assert "in-sample" in m["backtest"]["note"] or "표본 내" in m["backtest"]["note"]


# --- 자기상관 정량화 ---
def test_lag1_autocorrelation_bounded():
    """자기상관계수는 항상 [-1, 1] 범위 안에 있어야 한다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    corr = lag1_autocorrelation(port_ret)
    assert -1.0 <= corr <= 1.0


def test_lag1_autocorrelation_detects_trend():
    """단조 추세(강한 자기상관) 데이터는 상관계수가 뚜렷하게 양수여야 한다."""
    trending = np.arange(100, dtype=float) * 0.001
    assert lag1_autocorrelation(trending) > 0.9


def test_lag1_autocorrelation_short_series_returns_zero():
    """관측치가 너무 적으면(3개 미만) 0.0을 방어적으로 반환한다."""
    assert lag1_autocorrelation(np.array([0.01, 0.02])) == 0.0


def test_lag1_autocorrelation_constant_series_returns_zero_without_warning():
    """[리뷰 반영] 전 구간 수익률이 동일하면(표준편차 0) 경고 없이 0.0을 반환한다.

    np.corrcoef는 표준편차가 0이면 0으로 나누어 RuntimeWarning과 함께 nan을
    반환한다 — 사전에 방어해야 한다(예: 현금 전용 포트폴리오).
    """
    import warnings

    constant = np.full(10, 0.0001)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # 경고가 나면 예외로 승격시켜 실패시킨다
        assert lag1_autocorrelation(constant) == 0.0


@pytest.mark.parametrize(
    "values",
    [
        [1.0, 1.0, 1.0, 2.0],
        [1.0, 2.0, 2.0, 2.0],
    ],
)
def test_lag1_autocorrelation_one_sided_constant_returns_zero_without_warning(
    values,
):
    """lagged 벡터 한쪽만 상수여도 0 나눗셈 경고 없이 0.0을 반환한다."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert lag1_autocorrelation(np.array(values)) == 0.0


def test_autocorrelation_present_in_compute_metrics_meta():
    """compute_metrics의 meta에 autocorrelation_lag1이 포함된다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    m = compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10])
    assert -1.0 <= m["meta"]["autocorrelation_lag1"] <= 1.0
# --- 실데이터 경로 (yfinance + parquet 캐시) ---
# 실데이터 캐시(data/returns_real.parquet)는 Yahoo Finance 재배포 제약 때문에
# git에 커밋하지 않는다(로컬 전용). 그래서 아래 테스트들은 레포에 실제 캐시
# 파일이 있는지에 기대지 않고, _fetch_real_returns를 스텁으로 바꿔 각자
# tmp_path에 자기만의 캐시를 만들어 검증한다 — CI·새 클론에서도 네트워크 없이
# 항상 통과해야 한다.
def _fake_fetch_real_returns(n, as_of_date, rf_annual):
    idx = pd.bdate_range(end=pd.Timestamp(as_of_date), periods=n)
    return pd.DataFrame({c: 0.001 for c in ASSET_CLASSES}, index=idx)[ASSET_CLASSES]


def test_load_real_returns_reads_local_cache_offline(tmp_path, monkeypatch):
    """로컬에 미리 캐시가 있으면(발표 전 1회 예열) 이후 네트워크 없이도 동작한다.

    R7 요구사항(현장 시연은 인터넷 없이도 동작해야 함)의 핵심 전제를 검증한다.
    캐시를 git에 커밋하는 대신 "미리 한 번 실행해 로컬에 캐시를 남긴다"는
    전략(VVIP_PB_Advisor와 동일 패턴)을 그대로 재현한다.
    """
    monkeypatch.setattr(returns_mod, "_fetch_real_returns", _fake_fetch_real_returns)
    cache_path = tmp_path / "real.parquet"
    meta_path = tmp_path / "real.meta.json"

    # 1회차 — "발표 전 예열": 네트워크(스텁) 호출로 캐시를 만든다.
    load_real_returns(n=1250, as_of_date="2026-07-03", cache_path=cache_path, meta_path=meta_path)

    # 2회차 — "발표 당일": 재조회가 발생하면 즉시 실패하게 만들어 오프라인 보장을 증명한다.
    def fail_if_called(*a, **kw):
        raise AssertionError("캐시가 있는데도 재조회가 발생했다 — 오프라인 보장이 깨짐")

    monkeypatch.setattr(returns_mod, "_fetch_real_returns", fail_if_called)
    df = load_real_returns(n=1250, as_of_date="2026-07-03", cache_path=cache_path, meta_path=meta_path)
    assert df.shape == (1250, len(ASSET_CLASSES))
    assert list(df.columns) == ASSET_CLASSES
    assert not df.isna().any().any()


def test_corrupted_cache_with_matching_meta_is_not_trusted(tmp_path, monkeypatch):
    """[리뷰 반영] meta만 일치하면 parquet 내용을 검증 없이 반환하던 취약점.

    n=1250·as_of=2026-07-03 meta에 실제로는 1행·종료일 2000-01-01짜리
    손상된 parquet를 심어도, 예전 코드는 그대로 (1, 6)을 반환했다. 이제는
    캐시 히트 직후 내용을 검증해 불일치 시 재수집해야 한다.
    """
    cache_path = tmp_path / "real.parquet"
    meta_path = tmp_path / "real.meta.json"

    request = {
        "n": 1250,
        "as_of_date": "2026-07-03",
        "rf_annual": returns_mod.DEFAULT_RF_ANNUAL,
        "tickers": returns_mod.REAL_ASSET_TICKERS,
        "fx_ticker": returns_mod.FX_TICKER,
    }
    meta_path.write_text(json.dumps(request), encoding="utf-8")
    bad_df = pd.DataFrame({c: [0.0] for c in ASSET_CLASSES}, index=pd.DatetimeIndex(["2000-01-01"]))
    bad_df.to_parquet(cache_path)

    calls = []

    def fake_fetch(n, as_of_date, rf_annual):
        calls.append(n)
        return _fake_fetch_real_returns(n, as_of_date, rf_annual)

    monkeypatch.setattr(returns_mod, "_fetch_real_returns", fake_fetch)

    df = load_real_returns(n=1250, as_of_date="2026-07-03", cache_path=cache_path, meta_path=meta_path)
    assert calls == [1250]  # 손상된 캐시를 신뢰하지 않고 재수집했다
    assert len(df) == 1250


def test_cache_with_extra_column_is_not_trusted(tmp_path, monkeypatch):
    """[리뷰 반영] 행 수·컬럼이 일부 맞아도 예상 밖의 추가 컬럼이 섞여 있으면 재수집한다.

    df[ASSET_CLASSES] 슬라이싱 *이후*에 컬럼을 검사하면, 슬라이싱 자체가
    추가 컬럼을 조용히 버려서 검증이 항상 통과해버린다 — 슬라이싱 전
    원본에서 검사해야 오염된 캐시를 잡을 수 있다.
    """
    cache_path = tmp_path / "real.parquet"
    meta_path = tmp_path / "real.meta.json"
    request = {
        "n": 10,
        "as_of_date": "2026-07-03",
        "rf_annual": returns_mod.DEFAULT_RF_ANNUAL,
        "tickers": returns_mod.REAL_ASSET_TICKERS,
        "fx_ticker": returns_mod.FX_TICKER,
    }
    meta_path.write_text(json.dumps(request), encoding="utf-8")
    idx = pd.bdate_range(end="2026-07-03", periods=10)
    bad_df = pd.DataFrame({c: 0.001 for c in ASSET_CLASSES}, index=idx)
    bad_df["_unexpected_extra_column"] = 0.5  # 오염 흔적 — 정상 캐시엔 없어야 함
    bad_df.to_parquet(cache_path)

    calls = []

    def fake_fetch(n, as_of_date, rf_annual):
        calls.append(n)
        return _fake_fetch_real_returns(n, as_of_date, rf_annual)

    monkeypatch.setattr(returns_mod, "_fetch_real_returns", fake_fetch)

    df = load_real_returns(n=10, as_of_date="2026-07-03", cache_path=cache_path, meta_path=meta_path)
    assert calls == [10]  # 오염된 캐시를 신뢰하지 않고 재수집했다
    assert list(df.columns) == ASSET_CLASSES


def test_cache_with_abnormal_span_is_not_trusted(tmp_path, monkeypatch):
    """[리뷰 반영] 관측치 수(n)는 맞아도 실제 날짜 범위가 비정상으로 짧으면 재수집한다.

    n=1250개의 타임스탬프를 하루도 안 되는 구간(분 단위)에 몰아넣으면
    행 수·컬럼·결측치·정렬 체크는 전부 통과하지만, 실제 5년 관측이라는
    전제와는 완전히 어긋난다 — 시작일 기반 기간 검증이 필요한 이유.
    """
    cache_path = tmp_path / "real.parquet"
    meta_path = tmp_path / "real.meta.json"
    request = {
        "n": 1250,
        "as_of_date": "2026-07-03",
        "rf_annual": returns_mod.DEFAULT_RF_ANNUAL,
        "tickers": returns_mod.REAL_ASSET_TICKERS,
        "fx_ticker": returns_mod.FX_TICKER,
    }
    meta_path.write_text(json.dumps(request), encoding="utf-8")
    idx = pd.date_range(end="2026-07-03", periods=1250, freq="min")
    bad_df = pd.DataFrame({c: 0.001 for c in ASSET_CLASSES}, index=idx)
    bad_df.to_parquet(cache_path)

    calls = []

    def fake_fetch(n, as_of_date, rf_annual):
        calls.append(n)
        return _fake_fetch_real_returns(n, as_of_date, rf_annual)

    monkeypatch.setattr(returns_mod, "_fetch_real_returns", fake_fetch)

    load_real_returns(n=1250, as_of_date="2026-07-03", cache_path=cache_path, meta_path=meta_path)
    assert calls == [1250]  # 기간이 비정상적으로 짧은 캐시를 신뢰하지 않고 재수집했다


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


def _load_real_returns_with_tmp_cache(tmp_path):
    """load_real_returns의 cache_path/meta_path 기본값을 tmp 경로로 고정한 래퍼.

    _load_returns_with_tmp_cache와 동일한 이유(기본값은 함수 정의 시점에
    바인딩됨)로, var_engine이 참조하는 load_real_returns 자체를 교체해야
    테스트가 로컬 실데이터 캐시를 건드리지 않는다.
    """
    return functools.partial(
        returns_mod.load_real_returns, cache_path=tmp_path / "real.parquet", meta_path=tmp_path / "real.meta.json"
    )


def test_var_engine_uses_real_data_by_default(tmp_path, monkeypatch):
    """data_source 미지정 시 기본값 real — fx_applied=True, 출처 메타(tickers 등) 포함."""
    monkeypatch.setattr(returns_mod, "_fetch_real_returns", _fake_fetch_real_returns)
    monkeypatch.setattr("app.nodes.var_engine.load_real_returns", _load_real_returns_with_tmp_cache(tmp_path))
    state = {
        "run_config": {"as_of_date": "2026-07-03", "var_lookback_days": 10},
        "portfolio": PORTFOLIO,
    }
    result = var_engine(state)
    meta = result["metrics"]["meta"]
    assert meta["fx_applied"] is True
    assert meta["n_observations"] == 10
    assert meta["methodology_ref"] == "methodology_var_cvar_2026"
    assert meta["data_source"] == "real"
    assert meta["tickers"]["domestic_equity"] == "^KS11"
    assert meta["fx_ticker"] == "KRW=X"


def test_var_engine_real_path_defaults_to_1250_when_lookback_unset(tmp_path, monkeypatch):
    """[리뷰 반영] var_lookback_days가 없으면 real 경로는 방법론 문서와 일치하는
    1,250(returns.DEFAULT_REAL_N)을 써야 한다 — 더미 전용 DEFAULT_N(250)이 아니다."""
    monkeypatch.setattr(returns_mod, "_fetch_real_returns", _fake_fetch_real_returns)
    monkeypatch.setattr("app.nodes.var_engine.load_real_returns", _load_real_returns_with_tmp_cache(tmp_path))
    state = {"run_config": {"as_of_date": "2026-07-03"}, "portfolio": PORTFOLIO}
    result = var_engine(state)
    assert result["metrics"]["meta"]["n_observations"] == 1250


def test_var_engine_preserves_rf_rate_zero(monkeypatch):
    """[리뷰 반영] rf_rate=0.0은 유효한 설정이며 DEFAULT_RF_ANNUAL(3.25%)로 덮어쓰지 않는다."""
    captured = {}

    def fake_load_real_returns(n, as_of_date, rf_annual):
        captured["rf_annual"] = rf_annual
        return _fake_fetch_real_returns(n, as_of_date, rf_annual)

    monkeypatch.setattr("app.nodes.var_engine.load_real_returns", fake_load_real_returns)
    state = {
        "run_config": {"as_of_date": "2026-07-03", "var_lookback_days": 10, "rf_rate": 0.0},
        "portfolio": PORTFOLIO,
    }
    var_engine(state)
    assert captured["rf_annual"] == 0.0


def test_var_engine_rejects_unsupported_data_source():
    """[리뷰 반영] data_source 오타("reel" 등)를 조용히 dummy로 처리하지 않고 즉시 실패한다."""
    state = {
        "run_config": {"as_of_date": "2026-07-03", "data_source": "reel"},
        "portfolio": PORTFOLIO,
    }
    with pytest.raises(ValueError):
        var_engine(state)


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
    meta = result["metrics"]["meta"]
    assert meta["fx_applied"] is False
    assert meta["data_source"] == "dummy"
    assert meta["tickers"] is None
    assert meta["fx_ticker"] is None


def test_stress_shock_contract_locked():
    """[리뷰 반영] 충격값·포폴 결과는 문서/RAG 확정 계약값이므로 정확히 고정한다."""
    assert SCENARIO_A_HIGH_RATE["shocks"] == {
        "domestic_equity": -0.25, "global_equity": -0.25, "domestic_bond": -0.15,
        "global_bond": -0.12, "alternatives": -0.10, "cash": 0.0,
    }
    assert SCENARIO_B_STRONG_USD["shocks"] == {
        "domestic_equity": -0.12, "global_equity": -0.03, "domestic_bond": -0.05,
        "global_bond": -0.01, "alternatives": -0.02, "cash": 0.0,
    }
    res = run_all_stress(PORTFOLIO)
    assert res["A_high_rate"]["loss_pct"] == 0.178
    assert res["A_high_rate"]["loss_krw"] == 890_000_000.0
    assert res["B_strong_usd"]["loss_pct"] == 0.052
    assert res["B_strong_usd"]["loss_krw"] == 260_000_000.0
    assert SCENARIO_C_COVID["shocks"] == {
        "domestic_equity": -0.30, "global_equity": -0.25, "domestic_bond": -0.03,
        "global_bond": -0.01, "alternatives": -0.02, "cash": 0.0,
    }
    assert res["C_covid"]["loss_pct"] == 0.136
    assert res["C_covid"]["loss_krw"] == 680_000_000.0
    # 손실 순서 A > C > B
    assert res["A_high_rate"]["loss_krw"] > res["C_covid"]["loss_krw"] > res["B_strong_usd"]["loss_krw"]


# --- 부트스트랩 신뢰구간 (위조정밀도 방지) ---
def test_bootstrap_ci_reproducible():
    """[리뷰 반영] 같은 seed면 리샘플링 결과가 완전히 동일해야 한다(재현성)."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    ci1 = bootstrap_var_cvar_ci(port_ret, confidence=0.99, seed=42)
    ci2 = bootstrap_var_cvar_ci(port_ret, confidence=0.99, seed=42)
    assert ci1 == ci2


def test_bootstrap_ci_different_seed_can_differ():
    """시드가 다르면 리샘플링 결과가 달라질 수 있다(랜덤성이 실제로 작동하는지 확인).

    [리뷰 반영] 반환 딕셔너리 자체에 "seed" 필드가 들어있어서, 딕셔너리
    전체를 비교하면 실제 계산된 경계값(var/cvar low/high)이 우연히
    똑같아도(예: RNG가 seed를 무시하는 버그가 있어도) seed 값 차이만으로
    항상 통과해버린다. 실제 산출된 경계값만 비교해야 랜덤성이 진짜
    작동하는지 검증된다.
    """
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    ci_a = bootstrap_var_cvar_ci(port_ret, confidence=0.99, seed=1)
    ci_b = bootstrap_var_cvar_ci(port_ret, confidence=0.99, seed=2)
    bounds_a = {k: v for k, v in ci_a.items() if k not in ("seed", "ci_level", "n_bootstrap")}
    bounds_b = {k: v for k, v in ci_b.items() if k not in ("seed", "ci_level", "n_bootstrap")}
    assert bounds_a != bounds_b


def test_bootstrap_ci_low_le_high():
    """신뢰구간 하한은 항상 상한 이하여야 한다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    ci = bootstrap_var_cvar_ci(port_ret, confidence=0.99, seed=42)
    assert ci["var_pct_low"] <= ci["var_pct_high"]
    assert ci["cvar_pct_low"] <= ci["cvar_pct_high"]


# --- [리뷰 반영] 부트스트랩 입력값 방어적 유효성 검증 ---
def test_bootstrap_ci_rejects_empty_returns():
    with pytest.raises(ValueError):
        bootstrap_var_cvar_ci(np.array([]), confidence=0.99, seed=42)


def test_bootstrap_ci_rejects_invalid_confidence():
    port_ret = _wave_returns(scale=0.01)
    with pytest.raises(ValueError):
        bootstrap_var_cvar_ci(port_ret, confidence=1.5, seed=42)
    with pytest.raises(ValueError):
        bootstrap_var_cvar_ci(port_ret, confidence=0.0, seed=42)


def test_bootstrap_ci_rejects_invalid_ci_level():
    port_ret = _wave_returns(scale=0.01)
    with pytest.raises(ValueError):
        bootstrap_var_cvar_ci(port_ret, ci_level=1.0, seed=42)


def test_bootstrap_ci_rejects_non_positive_n_bootstrap():
    port_ret = _wave_returns(scale=0.01)
    with pytest.raises(ValueError):
        bootstrap_var_cvar_ci(port_ret, n_bootstrap=0, seed=42)


def test_bootstrap_ci_contains_point_estimate():
    """신뢰구간은 점추정치(historical_var/cvar)를 포함하는 게 일반적이다(90% CI 기준)."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    point_var = historical_var(port_ret, 0.99)
    point_cvar = historical_cvar(port_ret, 0.99)
    ci = bootstrap_var_cvar_ci(port_ret, confidence=0.99, ci_level=0.90, seed=42)
    assert ci["var_pct_low"] <= point_var <= ci["var_pct_high"]
    assert ci["cvar_pct_low"] <= point_cvar <= ci["cvar_pct_high"]


# --- VaR 신뢰구간(순서통계량, 무작위성 없음) ---
def test_var_ci_order_statistic_deterministic_without_seed():
    """순서통계량 방식은 seed 인자 자체가 없다 — 몇 번을 호출해도 완전히 동일하다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    ci1 = var_ci_order_statistic(port_ret, confidence=0.99, ci_level=0.90)
    ci2 = var_ci_order_statistic(port_ret, confidence=0.99, ci_level=0.90)
    assert ci1 == ci2


def test_var_ci_order_statistic_low_le_high():
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    ci = var_ci_order_statistic(port_ret, confidence=0.99, ci_level=0.90)
    assert ci["var_pct_low"] <= ci["var_pct_high"]


def test_var_ci_order_statistic_contains_point_estimate():
    """신뢰구간은 점추정치(historical_var)를 포함하는 게 일반적이다(90% CI 기준)."""
    df = _generate_dummy_returns(n=1250, as_of_date="2026-07-03")
    port_ret = portfolio_returns(df, PORTFOLIO)
    point_var = historical_var(port_ret, 0.99)
    ci = var_ci_order_statistic(port_ret, confidence=0.99, ci_level=0.90)
    assert ci["var_pct_low"] <= point_var <= ci["var_pct_high"]


def test_var_ci_order_statistic_width_shrinks_as_n_grows():
    """표본이 클수록 추정이 정밀해져 신뢰구간 폭(점추정치 대비 비율)이 줄어든다."""
    def _ratio(n):
        r = _wave_returns(scale=0.012, n=n)
        pt = historical_var(r, 0.99)
        ci = var_ci_order_statistic(r, confidence=0.99, ci_level=0.90)
        return (ci["var_pct_high"] - ci["var_pct_low"]) / pt

    assert _ratio(250) > _ratio(1250) > _ratio(5000)


def test_var_ci_order_statistic_rejects_empty_returns():
    with pytest.raises(ValueError):
        var_ci_order_statistic(np.array([]), confidence=0.99)


def test_var_ci_order_statistic_rejects_invalid_confidence():
    port_ret = _wave_returns(scale=0.01)
    with pytest.raises(ValueError):
        var_ci_order_statistic(port_ret, confidence=1.5)
    with pytest.raises(ValueError):
        var_ci_order_statistic(port_ret, confidence=0.0)


def test_var_ci_order_statistic_rejects_invalid_ci_level():
    port_ret = _wave_returns(scale=0.01)
    with pytest.raises(ValueError):
        var_ci_order_statistic(port_ret, ci_level=1.0)


def test_confidence_interval_present_in_compute_metrics_output():
    """compute_metrics 반환값에 horizon별 confidence_interval이 포함된다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    m = compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10], seed=42)
    assert "1d" in m["confidence_interval"]
    assert "10d" in m["confidence_interval"]
    assert m["confidence_interval"]["cvar_seed"] == 42
    assert m["confidence_interval"]["var_method"] == "order_statistic"
    assert m["confidence_interval"]["cvar_method"] == "bootstrap"
    assert m["meta"]["seed"] == 42


def test_confidence_interval_10d_wider_than_1d():
    """10일 신뢰구간은 √t 스케일링 때문에 1일보다 폭이 넓어야 한다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    m = compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10], seed=42)
    width_1d = m["confidence_interval"]["1d"]["var_pct_high"] - m["confidence_interval"]["1d"]["var_pct_low"]
    width_10d = m["confidence_interval"]["10d"]["var_pct_high"] - m["confidence_interval"]["10d"]["var_pct_low"]
    assert width_10d > width_1d


def test_compute_metrics_reproducible_with_confidence_interval():
    """같은 입력·같은 seed로 2회 호출 → confidence_interval을 포함해 완전히 동일해야 한다."""
    df = _generate_dummy_returns(n=250, as_of_date="2026-07-03")
    m1 = compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10], seed=42)
    m2 = compute_metrics(df, PORTFOLIO, confidence=0.99, horizons=[1, 10], seed=42)
    assert m1 == m2
    assert m1["meta"]["computation_hash"] == m2["meta"]["computation_hash"]


def test_var_engine_uses_config_seed_for_confidence_interval(tmp_path, monkeypatch):
    """var_engine이 run_config["seed"]를 읽어 신뢰구간 재현성에 쓴다."""
    monkeypatch.setattr(
        "app.nodes.var_engine.load_returns", _load_returns_with_tmp_cache(tmp_path)
    )
    state = {
        "run_config": {"as_of_date": "2026-07-03", "data_source": "dummy", "seed": 7},
        "portfolio": PORTFOLIO,
    }
    result = var_engine(state)
    assert result["metrics"]["meta"]["seed"] == 7


def test_stress_band_range_locked():
    """[range 밴드] 충격밴드 low/high도 확정 계약값이므로 정확히 고정한다."""
    res = run_all_stress(PORTFOLIO)
    assert res["A_high_rate"]["loss_pct_low"] == 0.13955
    assert res["A_high_rate"]["loss_pct_high"] == 0.21645
    assert res["A_high_rate"]["loss_krw_low"] == 697_750_000.0
    assert res["A_high_rate"]["loss_krw_high"] == 1_082_250_000.0
    assert res["B_strong_usd"]["loss_pct_low"] == 0.0405
    assert res["B_strong_usd"]["loss_pct_high"] == 0.0635
    assert res["C_covid"]["loss_pct_low"] == 0.103
    assert res["C_covid"]["loss_pct_high"] == 0.169
    for name in res:
        assert res[name]["loss_krw_low"] <= res[name]["loss_krw"] <= res[name]["loss_krw_high"]
