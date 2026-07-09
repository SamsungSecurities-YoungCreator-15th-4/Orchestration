"""결정론 계층 — 6자산군 일별 수익률 데이터 준비 + parquet 캐시.

주의: 이 패키지(app.engine)에서는 langchain/llm 관련 import 금지.
순수 결정론 데이터 계층 — 동일 입력에 대해 항상 동일한 수익률을 재현한다.

현재는 고정 수식 기반 더미 수익률을 생성한다(랜덤 미사용).
실데이터로 교체할 때는 이 모듈의 `_generate_dummy_returns`만 실제
시장데이터 로더로 바꾸면 되며, 그 외 계층(metrics/stress/node)은 그대로 둔다.
이것이 "더미 → 실데이터" 교체 지점(seam)이다.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

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
    if use_cache and cache_path.exists():
        return pd.read_parquet(cache_path)[ASSET_CLASSES]

    df = _generate_dummy_returns(n=n, as_of_date=as_of_date)
    if use_cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path)
    return df


def data_period(df: pd.DataFrame) -> dict:
    """수익률 데이터의 기간·관측수 메타데이터 (리포트 표기 규약용)."""
    return {
        "start": str(df.index.min().date()),
        "end": str(df.index.max().date()),
        "n_observations": int(len(df)),
    }
