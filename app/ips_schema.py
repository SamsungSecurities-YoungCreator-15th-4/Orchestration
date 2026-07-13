"""IPS 공개 스키마 진입점.

실제 팀 데이터 계약은 app.state가 SSOT이므로 중복 모델을 선언하지 않고 재노출한다.
"""
from app.state import (
    FIXED_AGE,
    FIXED_ASSET_EOK,
    FIXED_GOAL,
    FIXED_JOB,
    FIXED_RISK,
    IPSProfile,
    UNIQUE_PREFIX,
)

__all__ = [
    "FIXED_AGE",
    "FIXED_ASSET_EOK",
    "FIXED_GOAL",
    "FIXED_JOB",
    "FIXED_RISK",
    "IPSProfile",
    "UNIQUE_PREFIX",
]
