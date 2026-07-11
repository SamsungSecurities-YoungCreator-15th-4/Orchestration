"""결정론 계층 — 6자산군 일별 수익률 데이터 준비 + parquet 캐시.

주의: 이 패키지(app.engine)에서는 langchain/llm 관련 import 금지.
순수 결정론 데이터 계층 — 동일 입력에 대해 항상 동일한 수익률을 재현한다.
(yfinance는 시장데이터 조회 SDK일 뿐 LLM이 아니므로 이 규칙에 저촉되지 않는다.)

`load_returns`(더미, 고정 수식·랜덤 미사용)와 `load_real_returns`(실데이터,
yfinance 조회 + parquet 캐시) 두 경로를 제공한다. 어느 쪽을 쓸지는
`app.nodes.var_engine`이 `run_config["data_source"]`로 선택한다.
더미 경로는 테스트에서 네트워크 없이 빠르게 돌리기 위해 그대로 유지한다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 6자산군 — load_inputs.py 포트폴리오와 동일한 asset_class 키(순서 고정)
ASSET_CLASSES = [
    "domestic_equity",
    "global_equity",
    "domestic_bond",
    "global_bond",
    "alternatives",
    "cash",
]

# 자산군별 일간 변동성 스케일(더미 전용 특성) — 주식 > 대체 > 채권 > 현금.
# 실데이터 전환 시 무의미해지는 더미 전용 상수다.
_DUMMY_VOL = {
    "domestic_equity": 0.0130,
    "global_equity": 0.0115,
    "domestic_bond": 0.0035,
    "global_bond": 0.0045,
    "alternatives": 0.0080,
    "cash": 0.0002,
}
# 자산군별 위상차 — 자산 간 움직임을 비동조로 만들어 분산 효과가 나오도록.
_DUMMY_PHASE = {
    "domestic_equity": 0.0,
    "global_equity": 0.7,
    "domestic_bond": 1.6,
    "global_bond": 2.3,
    "alternatives": 3.1,
    "cash": 0.5,
}

DEFAULT_N = 250  # 약 1년(거래일) 관측
DEFAULT_AS_OF = "2026-07-03"
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "returns_dummy.parquet"


def _generate_dummy_returns(n: int = DEFAULT_N, as_of_date: str | None = None) -> pd.DataFrame:
    """고정 수식 기반 6자산군 더미 일별 수익률 (결정론적, 랜덤 미사용).

    실데이터 전환 시 이 함수만 실제 시장데이터 로더로 교체한다.
    반환: 거래일 DatetimeIndex × 6자산군 컬럼의 일별 수익률 DataFrame.
    """
    i = np.arange(n, dtype=float)
    data: dict[str, np.ndarray] = {}
    for ac in ASSET_CLASSES:
        vol = _DUMMY_VOL[ac]
        phase = _DUMMY_PHASE[ac]
        # 서로 다른 주파수/위상으로 자산 간 비동조 움직임을 만든다.
        data[ac] = (
            vol * np.sin(0.9 * i + phase)
            + 0.3 * vol * np.cos(0.35 * i + phase)
            - 0.00005
        )
    end = pd.Timestamp(as_of_date or DEFAULT_AS_OF)
    idx = pd.bdate_range(end=end, periods=n, name="date")
    return pd.DataFrame(data, index=idx)[ASSET_CLASSES]


def load_returns(
    n: int = DEFAULT_N,
    as_of_date: str | None = None,
    cache_path: Path | str = CACHE_PATH,
    use_cache: bool = True,
) -> pd.DataFrame:
    """6자산군 일별 수익률을 반환. parquet 캐시가 있으면 읽고, 없으면 생성 후 저장.

    - 캐시 히트 시에도 동일 데이터가 재현되므로 재현성에 영향이 없다.
    - 실데이터 전환 시에도 이 함수의 인터페이스(반환 스키마)는 유지한다.
    """
    cache_path = Path(cache_path)
    # 기대 종료일은 생성 로직(_generate_dummy_returns의 bdate_range)과 동일하게
    # 정규화한다. as_of_date가 비영업일이면 직전 영업일로 물러나므로, 원본
    # as_of_date로 비교하면 캐시가 영원히 미스 나며 매번 재기록한다.
    expected_end = pd.bdate_range(
        end=pd.Timestamp(as_of_date or DEFAULT_AS_OF), periods=1
    )[0].date()

    # 캐시가 요청 파라미터(n·종료일)와 일치할 때만 재사용한다.
    # 파라미터가 달라졌는데 낡은 캐시를 반환하면 재현성·정확성이 깨진다.
    if use_cache and cache_path.exists():
        try:
            cached = pd.read_parquet(cache_path)
            if len(cached) == n and cached.index.max().date() == expected_end:
                return cached[ASSET_CLASSES]
        except Exception as e:
            # 파라미터 불일치가 아니라 pyarrow 미설치·스키마 변경 등 재생성으로
            # 해결되지 않는 실패도 삼킬 수 있으므로 흔적을 남긴다.
            logger.warning("캐시 읽기 실패, 재생성합니다: %s (%s)", cache_path, e)

    df = _generate_dummy_returns(n=n, as_of_date=as_of_date)
    if use_cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path)
    return df


# --- 실데이터 경로 (yfinance) ---------------------------------------------
# 자산군별 대리 지수/ETF. domestic_* 는 KRW로 직접 상장돼 있어 그대로 쓰고,
# 해외 자산은 USD로 상장돼 있어 FX_TICKER(USD/KRW)로 원화 환산한다.
# methodology_var_cvar_2026 §7(환율 처리) 참조 — 실데이터 경로는 fx_applied=True.
REAL_ASSET_TICKERS = {
    "domestic_equity": "^KS11",     # KOSPI 지수 (KRW)
    "domestic_bond": "114260.KS",   # KODEX 국고채10년 (KRW)
    "global_equity": "ACWI",        # iShares MSCI ACWI (USD)
    "global_bond": "IGOV",          # iShares International Treasury Bond, 무헤지 (USD)
    "alternatives": "GLD",          # SPDR Gold Shares (USD)
}
USD_DENOMINATED = {"global_equity", "global_bond", "alternatives"}
FX_TICKER = "KRW=X"  # USD/KRW (1달러당 원화)

REAL_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "returns_real.parquet"
REAL_CACHE_META_PATH = Path(__file__).resolve().parents[2] / "data" / "returns_real.meta.json"
DEFAULT_RF_ANNUAL = 0.0325  # config.yaml의 rf_rate 기본값과 동일 — cash 자산군 수익률에 사용
# methodology_var_cvar_2026 §3에서 확정한 실데이터 관측기간(1,250거래일≈5년).
# DEFAULT_N(더미 전용, 250)과 값이 다르므로 별도 상수로 둔다 — 하나로 합치면
# var_lookback_days가 config에서 누락됐을 때 실데이터 경로가 조용히 250일로
# 폴백해 문서와 어긋난다.
DEFAULT_REAL_N = 1250


def _extract_close(data: pd.DataFrame, ticker: str) -> pd.Series:
    """yf.download() 결과에서 종가 Series를 안전하게 뽑는다.

    yfinance는 버전/호출 방식에 따라 단일 티커 조회에도 (Close, ticker) 형태의
    MultiIndex 컬럼을 반환하거나, 단순 flat 컬럼(Close 하나)을 반환할 수 있다.
    flat인데 무조건 data["Close"][ticker]로 인덱싱하면 KeyError가 난다.
    """
    close = data["Close"]
    if isinstance(close, pd.DataFrame):
        close = close[ticker]
    return close


def _apply_fx_conversion(pct: pd.DataFrame, fx_ret: pd.Series, rf_annual: float) -> pd.DataFrame:
    """현지통화 수익률에 USD/KRW 변동을 복리 결합해 원화 환산 수익률로 만든다.

    methodology_var_cvar_2026 §3(기준통화 및 환율 처리): r_KRW = (1+r_USD)*(1+r_FX) - 1.
    USD_DENOMINATED 자산군만 환산하고, 원화 상장 자산은 그대로 둔다.
    cash는 시장데이터가 없으므로 rf_annual/252 상수로 채운다(결정론적).
    yfinance 호출과 분리된 순수 함수라 네트워크 없이 단위테스트할 수 있다.
    """
    returns = pd.DataFrame(index=pct.index)
    for ac in REAL_ASSET_TICKERS:
        if ac in USD_DENOMINATED:
            returns[ac] = (1.0 + pct[ac]) * (1.0 + fx_ret) - 1.0
        else:
            returns[ac] = pct[ac]
    returns["cash"] = rf_annual / 252.0
    return returns[ASSET_CLASSES]


def _fetch_real_returns(
    n: int = DEFAULT_REAL_N,
    as_of_date: str | None = None,
    rf_annual: float = DEFAULT_RF_ANNUAL,
) -> pd.DataFrame:
    """yfinance로 6자산군 실데이터를 조회해 일별 원화 환산 수익률로 변환한다.

    - 해외자산(USD 상장)은 현지통화 수익률 × USD/KRW 환율변동을 복리 결합해
      원화 환산 총수익률로 만든다: r_KRW = (1+r_USD)*(1+r_fx) - 1.
    - domestic_* 는 KRW로 직접 상장돼 있어 환산이 필요 없다.
    - cash는 시장데이터가 없으므로 rf_annual/252 상수로 둔다(결정론적).
    - 한국·미국 거래일이 서로 달라(공휴일 불일치) 직전 종가로 순방향 채움(ffill)한
      뒤 그래도 남는 선행 결측치만 제거한다 — 단순 dropna는 양쪽 거래소 중 한
      곳만 휴장해도 그 날 전체를 버려 연간 20~25거래일이 손실되기 때문이다.
    """
    import yfinance as yf  # 지연 import — 더미 경로(테스트 기본 경로)는 네트워크 의존성이 없어야 한다.

    end = pd.Timestamp(as_of_date or DEFAULT_AS_OF)
    # 정렬 후 휴장일로 줄어드는 분량을 감안해 넉넉히 더 긴 기간을 요청한다.
    start = end - pd.Timedelta(days=int(n * 2.5) + 30)

    closes: dict[str, pd.Series] = {}
    for ac, ticker in REAL_ASSET_TICKERS.items():
        data = yf.download(
            ticker, start=start, end=end + pd.Timedelta(days=1),
            progress=False, auto_adjust=True,
        )
        if data.empty:
            raise ValueError(f"실데이터 조회 실패(빈 응답): {ticker} ({ac})")
        closes[ac] = _extract_close(data, ticker)

    fx_data = yf.download(
        FX_TICKER, start=start, end=end + pd.Timedelta(days=1),
        progress=False, auto_adjust=True,
    )
    if fx_data.empty:
        raise ValueError(f"환율 데이터 조회 실패(빈 응답): {FX_TICKER}")

    prices = pd.DataFrame(closes)
    prices["_fx"] = _extract_close(fx_data, FX_TICKER)
    # 한쪽 거래소만 휴장한 날은 직전 종가로 채워 보존하고, 그래도 못 채우는
    # 선행 구간(상장 전 등)만 제거한다.
    prices = prices.sort_index().ffill().dropna()

    pct = prices.pct_change().dropna()  # 첫 행(변화율 계산 불가) 제거
    returns = _apply_fx_conversion(pct[list(REAL_ASSET_TICKERS)], pct["_fx"], rf_annual)

    returns = returns[returns.index <= end]
    if len(returns) < n:
        raise ValueError(
            f"실데이터 정렬 후 관측치가 부족합니다: {len(returns)}건 (요청 n={n}). "
            "조회 기간을 늘리거나 n을 줄이세요."
        )
    return returns.tail(n)


def _validate_cached_real_returns(df: pd.DataFrame, n: int, as_of_date: str | None) -> None:
    """캐시에서 읽은 실데이터가 신뢰할 수 있는 모양인지 검증한다.

    사이드카 meta는 '요청 파라미터'만 기록하므로, parquet 파일 내용 자체가
    손상되거나(예: 다른 실행이 쓰다 만 파일) 다른 데이터로 바뀌어도 meta만
    일치하면 그대로 반환되는 취약점이 있었다(리뷰 지적 — n=1250·as_of=기준일
    meta에 1행·종료일 2000-01-01짜리 parquet를 넣어도 그대로 캐시 히트됨을
    재현). 캐시 히트 직후 실제 내용을 검증해, 불일치 시 예외를 던져 재수집
    경로(load_real_returns의 except 블록)를 타게 한다.
    """
    if len(df) != n:
        raise ValueError(f"캐시 관측치 수 불일치: 기대 {n}건, 실제 {len(df)}건")
    if list(df.columns) != ASSET_CLASSES:
        raise ValueError(f"캐시 컬럼 불일치: {list(df.columns)}")
    if df.isna().any().any():
        raise ValueError("캐시에 결측치가 있습니다")
    if not df.index.is_unique or not df.index.is_monotonic_increasing:
        raise ValueError("캐시 인덱스가 정렬되지 않았거나 날짜가 중복됩니다")
    expected_end = pd.Timestamp(as_of_date or DEFAULT_AS_OF)
    if abs((df.index.max() - expected_end).days) > 10:
        raise ValueError(
            f"캐시 종료일이 기준일과 크게 어긋납니다: "
            f"{df.index.max().date()} (기준일 {expected_end.date()})"
        )


def load_real_returns(
    n: int = DEFAULT_REAL_N,
    as_of_date: str | None = None,
    cache_path: Path | str = REAL_CACHE_PATH,
    meta_path: Path | str = REAL_CACHE_META_PATH,
    use_cache: bool = True,
    rf_annual: float = DEFAULT_RF_ANNUAL,
) -> pd.DataFrame:
    """실데이터(yfinance) 6자산군 일별 수익률을 반환. parquet 캐시 우선.

    캐시 유효성은 요청 파라미터(n·as_of_date·rf_annual·티커셋)를 사이드카
    JSON(meta_path)에 기록해 정확히 일치할 때만 재사용한다 — 실제 거래소
    휴장일 캘린더를 코드로 예측하지 않고 요청 파라미터 자체를 키로 쓰는
    방식이라 `load_returns`의 영업일 추정 방식보다 더 안전하다.
    """
    cache_path = Path(cache_path)
    meta_path = Path(meta_path)
    request = {
        "n": n,
        "as_of_date": str(as_of_date or DEFAULT_AS_OF),
        "rf_annual": rf_annual,
        "tickers": REAL_ASSET_TICKERS,
        "fx_ticker": FX_TICKER,
    }

    if use_cache and cache_path.exists() and meta_path.exists():
        try:
            cached_request = json.loads(meta_path.read_text(encoding="utf-8"))
            if cached_request == request:
                cached_df = pd.read_parquet(cache_path)[ASSET_CLASSES]
                _validate_cached_real_returns(cached_df, n, as_of_date)
                return cached_df
        except Exception as e:
            logger.warning("실데이터 캐시 읽기 실패, 재수집합니다: %s (%s)", cache_path, e)

    df = _fetch_real_returns(n=n, as_of_date=as_of_date, rf_annual=rf_annual)
    if use_cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path)
        meta_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    return df


def data_period(df: pd.DataFrame) -> dict:
    """수익률 데이터의 기간·관측수 메타데이터 (리포트 표기 규약용)."""
    return {
        "start": str(df.index.min().date()),
        "end": str(df.index.max().date()),
        "n_observations": int(len(df)),
    }
