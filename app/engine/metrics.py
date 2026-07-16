"""결정론 계층 — historical VaR/CVaR 계산.

주의: 이 패키지(app.engine)에서는 langchain/llm 관련 import 금지.
순수 수치 계산만 수행하며, 동일 입력에 대해 항상 동일 출력을 보장한다.
"""
import math

import numpy as np
import pandas as pd
from scipy.stats import norm

from app.engine.stress import run_all_stress
from app.utils.hashing import sha256_of_dict


def historical_var(returns: np.ndarray, confidence: float = 0.99) -> float:
    """Historical VaR: 수익률 분포의 (1-confidence) 분위수 손실 (양수 = 손실).

    변동성이 매우 낮은(또는 0인) 입력에서는 해당 분위수 자체가 양수로 나와
    VaR이 음수로 계산될 수 있다 — 버그가 아니라 "해당 신뢰수준에서 손실이
    발생하지 않는다(오히려 최소 이익이 보장된다)"는 뜻이다. 예: 현금 100%
    포트폴리오처럼 일별 수익률이 항상 양수인 경우.
    """
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
    """자산군별 일별 수익률 × 금액 비중으로 포트폴리오 일별 수익률 시계열 합성.

    용어 유의: 방법론 문서는 이 방식을 "buy-and-hold 근사"로 표현하지만,
    엄밀히는 buy-and-hold(무거래 시 시세 변동에 따라 비중이 자연스럽게
    흘러가는 것)가 아니라 "고정 비중(constant-mix)" 계산이다 — 현재
    포트폴리오 비중을 과거 전 구간에 동일하게 적용할 뿐, 일별 리밸런싱
    거래를 가정하거나 비중이 시세에 따라 흘러가도록 반영하지 않는다.
    다만 이는 Historical Simulation VaR의 표준적인 계산 방식(현재 비중을
    과거 시나리오에 적용)이므로 계산 자체는 정확하다.
    """
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
        if w == 0.0:
            contributions[c] = 0.0
            continue
        avg_asset_return = float(returns_df[c].to_numpy()[tail_mask].mean())
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
    lagged_left = port_ret[:-1]
    lagged_right = port_ret[1:]
    if (
        np.all(lagged_left == lagged_left[0])
        or np.all(lagged_right == lagged_right[0])
    ):
        # lagged 벡터 중 하나라도 표준편차가 0이면 corrcoef가 0으로 나눈다.
        return 0.0
    corr = np.corrcoef(lagged_left, lagged_right)[0, 1]
    return 0.0 if np.isnan(corr) else round(float(corr), 6)


def var_ci_order_statistic(
    port_ret: np.ndarray,
    confidence: float = 0.99,
    ci_level: float = 0.90,
) -> dict:
    """VaR(1일)의 분포무관(distribution-free) 신뢰구간 — 순서통계량 기반, 난수 미사용.

    복원추출(bootstrap) 없이, 정렬된 과거 수익률에서 이항분포의 정규근사로
    유도한 순서통계량 위치를 직접 계산해 신뢰구간을 반환한다. n개 관측치 중
    (1-confidence) 분위수 이하 관측치 개수는 Binomial(n, 1-confidence)을
    따른다는 사실에서, 그 이항분포의 정규근사로 신뢰구간 경계에 해당하는
    순서통계량 인덱스(j, k)를 구한다.

    무작위성이 전혀 없어(정렬 + 산술 연산뿐) 시드 없이도 항상 완전히 동일한
    결과를 재현한다 — bootstrap_var_cvar_ci보다 더 강한 형태의 재현성이다.
    다만 정규근사 자체가 표본이 충분히 클 때 유효하므로, 극단적으로 작은
    표본에서는 근사 오차가 커질 수 있다.

    CVaR(꼬리 구간의 평균)에는 이 방법이 직접 적용되지 않는다 — 순서통계량
    이론은 단일 분위수(포인트)에 대한 것이라, 꼬리 평균인 CVaR의 신뢰구간은
    여전히 bootstrap_var_cvar_ci를 쓴다.
    """
    port_ret = np.asarray(port_ret, dtype=float)
    n = len(port_ret)
    if n == 0:
        raise ValueError("수익률 데이터가 비어 있어 신뢰구간을 계산할 수 없습니다.")
    if not (0.0 < confidence < 1.0):
        raise ValueError("신뢰수준(confidence)은 0과 1 사이의 값이어야 합니다.")
    if not (0.0 < ci_level < 1.0):
        raise ValueError("신뢰구간 수준(ci_level)은 0과 1 사이의 값이어야 합니다.")

    p = 1.0 - confidence
    z = float(norm.ppf(1.0 - (1.0 - ci_level) / 2.0))
    center = n * p
    half_width = z * math.sqrt(n * p * (1.0 - p))

    sorted_ret = np.sort(port_ret)
    j = max(math.floor(center - half_width), 0)
    k = min(math.ceil(center + half_width), n - 1)

    lower_q = float(sorted_ret[j])
    upper_q = float(sorted_ret[k])

    return {
        "ci_level": ci_level,
        "method": "order_statistic",
        "var_pct_low": round(-upper_q, 8),
        "var_pct_high": round(-lower_q, 8),
    }


def bootstrap_var_cvar_ci(
    port_ret: np.ndarray,
    confidence: float = 0.99,
    ci_level: float = 0.90,
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> dict:
    """VaR·CVaR(1일)의 부트스트랩 신뢰구간 — 위조정밀도 방지(range/신뢰구간 표현).

    포트폴리오 수익률을 복원추출(bootstrap resampling)로 n_bootstrap회
    다시 뽑아 매번 VaR·CVaR을 재계산하고, 그 분포의 (1±ci_level)/2 분위수를
    구간으로 반환한다. "1.478812%"처럼 근거 없이 정밀한 점추정치 대신
    "1.2%~1.7%" 같은 구간으로 표현할 수 있게 한다(methodology_var_cvar_2026
    §7 표기 규약).

    재현성: 리샘플링에 랜덤성이 들어가므로 seed를 반드시 고정한다
    (config.yaml의 seed와 동일 기본값 42) — 같은 입력이면 같은 구간이 나온다.
    """
    port_ret = np.asarray(port_ret, dtype=float)
    n = len(port_ret)
    if n == 0:
        raise ValueError("수익률 데이터가 비어 있어 부트스트랩을 수행할 수 없습니다.")
    if not (0.0 < confidence < 1.0):
        raise ValueError("신뢰수준(confidence)은 0과 1 사이의 값이어야 합니다.")
    if not (0.0 < ci_level < 1.0):
        raise ValueError("신뢰구간 수준(ci_level)은 0과 1 사이의 값이어야 합니다.")
    if n_bootstrap <= 0:
        raise ValueError("부트스트랩 반복 횟수(n_bootstrap)는 1 이상이어야 합니다.")
    rng = np.random.default_rng(seed)

    idx = rng.integers(0, n, size=(n_bootstrap, n))
    resamples = port_ret[idx]  # (n_bootstrap, n)

    q = np.quantile(resamples, 1.0 - confidence, axis=1)  # 리샘플별 VaR 분위수
    var_samples = -q

    tail_mask = resamples <= q[:, None]
    tail_sums = np.sum(np.where(tail_mask, resamples, 0.0), axis=1)
    tail_counts = np.maximum(tail_mask.sum(axis=1), 1)
    cvar_samples = -(tail_sums / tail_counts)

    lo_q = (1.0 - ci_level) / 2.0
    hi_q = 1.0 - lo_q
    return {
        "ci_level": ci_level,
        "n_bootstrap": n_bootstrap,
        "seed": seed,
        "var_pct_low": round(float(np.quantile(var_samples, lo_q)), 8),
        "var_pct_high": round(float(np.quantile(var_samples, hi_q)), 8),
        "cvar_pct_low": round(float(np.quantile(cvar_samples, lo_q)), 8),
        "cvar_pct_high": round(float(np.quantile(cvar_samples, hi_q)), 8),
    }


def compute_metrics(
    returns_df: pd.DataFrame,
    portfolio: list[dict],
    confidence: float = 0.99,
    horizons: list[int] | None = None,
    base_currency: str = "KRW",
    data_period_meta: dict | None = None,
    fx_applied: bool = False,
    methodology_ref: str | None = None,
    data_source: str | None = None,
    tickers: dict | None = None,
    fx_ticker: str | None = None,
    fx_rate_asof: float | None = None,
    seed: int = 42,
    ci_level: float = 0.90,
    n_bootstrap: int = 1000,
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
    # VaR 신뢰구간: 순서통계량 기반(무작위성 없음, seed 불필요).
    var_ci_1d = var_ci_order_statistic(port_ret, confidence=confidence, ci_level=ci_level)
    # CVaR 신뢰구간: 순서통계량 이론이 꼬리 평균(CVaR)에는 직접 적용되지 않아
    # 부트스트랩을 유지한다 — seed 고정으로 재현성을 보장한다.
    cvar_ci_1d = bootstrap_var_cvar_ci(
        port_ret, confidence=confidence, ci_level=ci_level, n_bootstrap=n_bootstrap, seed=seed
    )

    per_horizon = {}
    confidence_interval = {
        "ci_level": ci_level,
        "var_method": "order_statistic",
        "cvar_method": "bootstrap",
        "cvar_n_bootstrap": n_bootstrap,
        "cvar_seed": seed,
    }
    for h in horizons:
        scale = math.sqrt(h)
        per_horizon[f"{h}d"] = {
            "var_pct": round(var_1d * scale, 8),
            "cvar_pct": round(cvar_1d * scale, 8),
            "var_krw": round(total_value * var_1d * scale, 2),
            "cvar_krw": round(total_value * cvar_1d * scale, 2),
        }
        # √t 스케일링과 동일 규약으로 신뢰구간 경계도 보유기간별로 환산한다.
        confidence_interval[f"{h}d"] = {
            "var_pct_low": round(var_ci_1d["var_pct_low"] * scale, 8),
            "var_pct_high": round(var_ci_1d["var_pct_high"] * scale, 8),
            "cvar_pct_low": round(cvar_ci_1d["cvar_pct_low"] * scale, 8),
            "cvar_pct_high": round(cvar_ci_1d["cvar_pct_high"] * scale, 8),
            "var_krw_low": round(total_value * var_ci_1d["var_pct_low"] * scale, 2),
            "var_krw_high": round(total_value * var_ci_1d["var_pct_high"] * scale, 2),
            "cvar_krw_low": round(total_value * cvar_ci_1d["cvar_pct_low"] * scale, 2),
            "cvar_krw_high": round(total_value * cvar_ci_1d["cvar_pct_high"] * scale, 2),
        }

    stress = run_all_stress(portfolio)

    tail_contribution_krw = tail_contribution(returns_df, portfolio, confidence)
    cvar_1d_krw = per_horizon["1d"]["cvar_krw"] if "1d" in per_horizon else round(total_value * cvar_1d, 2)
    tail_contribution_pct = {
        c: round(v / cvar_1d_krw, 6) if cvar_1d_krw else 0.0
        for c, v in tail_contribution_krw.items()
    }
    drilldown = {
        "tail_contribution_krw": tail_contribution_krw,
        "tail_contribution_pct": tail_contribution_pct,
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
            "data_source": data_source,
            "tickers": tickers,
            "fx_ticker": fx_ticker,
            "fx_rate_asof": fx_rate_asof,
            "seed": seed,
            "ci_level": ci_level,
            "n_bootstrap": n_bootstrap,
        },
        "results": {
            "per_horizon": per_horizon,
            "stress": stress,
            "drilldown": drilldown,
            "backtest": backtest,
            "autocorrelation_lag1": autocorrelation_lag1,
            "confidence_interval": confidence_interval,
        },
    }

    return {
        "confidence": confidence,
        "horizons": per_horizon,
        "stress": stress,
        "drilldown": drilldown,
        "backtest": backtest,
        "confidence_interval": confidence_interval,
        "meta": {
            "method": "historical",
            "scaling": "sqrt_t",
            "n_observations": int(len(returns_df)),
            "base_currency": base_currency,
            "data_period": data_period_meta,
            "fx_applied": fx_applied,
            "autocorrelation_lag1": autocorrelation_lag1,
            "methodology_ref": methodology_ref,
            "data_source": data_source,
            "tickers": tickers,
            "fx_ticker": fx_ticker,
            "fx_rate_asof": fx_rate_asof,
            "seed": seed,
            "computation_hash": sha256_of_dict(payload),
        },
    }
