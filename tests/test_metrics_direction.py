"""VaR 방향성 검증: 변동성 큰 수익률의 VaR > 변동성 작은 수익률의 VaR."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.metrics import historical_cvar, historical_var


def _wave_returns(scale: float, n: int = 250) -> np.ndarray:
    """고정 수식 기반 더미 수익률 (결정론적, 랜덤 미사용)."""
    i = np.arange(n, dtype=float)
    return scale * np.sin(0.9 * i) + 0.3 * scale * np.cos(0.35 * i)


def test_var_direction_high_vol_greater():
    low_vol = _wave_returns(scale=0.005)
    high_vol = _wave_returns(scale=0.02)
    assert historical_var(high_vol, 0.99) > historical_var(low_vol, 0.99)


def test_cvar_gte_var():
    returns = _wave_returns(scale=0.012)
    assert historical_cvar(returns, 0.99) >= historical_var(returns, 0.99)
