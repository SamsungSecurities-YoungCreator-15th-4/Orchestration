from typing import Literal, TypedDict

from pydantic import BaseModel, Field, field_validator


FIXED_AGE = "50"
FIXED_GOAL = "시장리스크 진단·대응안을 엔진으로 산출·검증"
FIXED_ASSET_EOK = 50.0
FIXED_RISK = "균형형"
UNIQUE_PREFIX = "고금리·강달러 충격"


class IPSProfile(BaseModel):
    """고객 상담에서 추출하는 공개 IPS JSON 계약.

    Age·Goal·Asset·Risk는 과제 시나리오의 고정값이며 LLM이 변경할 수 없다.
    금액 필드의 단위는 억 원, Time의 단위는 년이다.
    """

    Name: str = Field(default="확인 필요", description="고객 이름")
    Age: Literal["50"] = FIXED_AGE
    Job: str = Field(default="확인 필요", description="고객 직업")
    Goal: Literal["시장리스크 진단·대응안을 엔진으로 산출·검증"] = FIXED_GOAL
    Asset: Literal[50.0] = FIXED_ASSET_EOK
    Return: float = Field(default=0.0, ge=0, description="목표 수익 금액, 단위 억 원")
    Risk: Literal["균형형"] = FIXED_RISK
    Time: float = Field(default=0.0, ge=0, description="투자기간, 단위 년")
    Tax: str = Field(default="확인 필요", description="세금 관련 사항")
    Liquidity: Literal["낮음", "중간", "높음"] = "중간"
    Legal: str = Field(default="해당 사항 없음", description="법적 제약")
    Unique: str = Field(default=UNIQUE_PREFIX, description="고객 특수 상황")

    @field_validator("Unique")
    @classmethod
    def unique_starts_with_required_shock(cls, value: str) -> str:
        detail = (value or "").strip()
        if detail.startswith(UNIQUE_PREFIX):
            return detail
        return f"{UNIQUE_PREFIX} · {detail}" if detail else UNIQUE_PREFIX


class RiskState(TypedDict, total=False):
    run_config: dict
    trace_id: str
    raw_input: str
    portfolio: list
    liquidity_required_krw: float | None
    market_data_ref: dict
    ips: dict
    conflicts: list
    conflict_retries: int
    approval: dict
    metrics: dict
    explanations: list
    citations: list
    judge: dict
    judge_retries: int
    judge_feedback: str
    report: dict
