"""결정론 리스크 엔진 호출 노드 — app.engine.metrics.compute_metrics() 위임.

더미 수익률은 고정 수식으로 생성한 배열(랜덤 미사용)이며,
동일 config 하에서 computation_hash가 항상 동일함을 보장한다.
"""
import numpy as np

from app.engine.metrics import compute_metrics
from app.state import RiskState


def _dummy_returns(n: int = 250, scale: float = 0.012) -> list[float]:
    """고정 수식 기반 더미 일간 수익률 (결정론적, 랜덤 미사용)."""
    i = np.arange(n, dtype=float)
    r = scale * np.sin(0.9 * i) + 0.004 * np.cos(0.35 * i) - 0.0002
    return [float(x) for x in r]


def var_engine(state: RiskState) -> dict:
    run_config = state.get("run_config") or {}
    metrics = compute_metrics(
        returns=_dummy_returns(),
        portfolio=state.get("portfolio", []),
        confidence=run_config.get("var_confidence", 0.99),
        horizons=run_config.get("horizons", [1, 10]),
    )
    return {"metrics": metrics}
