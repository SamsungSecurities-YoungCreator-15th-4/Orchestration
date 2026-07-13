from typing import Literal, TypedDict

from pydantic import BaseModel, Field, field_validator


FIXED_AGE = "50"
FIXED_JOB = "자영업자"
FIXED_GOAL = "시장리스크 진단·대응안을 엔진으로 산출·검증"
FIXED_ASSET_EOK = 50.0
FIXED_RISK = "균형형"
UNIQUE_PREFIX = "고금리·강달러 충격"


class IPSProfile(BaseModel):
    """고객 상담에서 추출하는 공개 IPS JSON 계약.

    Age·Job·Goal·Asset·Risk는 과제 시나리오의 고정값이며 LLM이 변경할 수 없다.
    금액 필드의 단위는 억 원, Time의 단위는 년이다.
    """

    Name: str = Field(default="확인 필요", description="고객 이름")
    Age: Literal["50"] = FIXED_AGE
    Job: Literal["자영업자"] = FIXED_JOB
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


ApprovalStatus = Literal["draft", "reviewed", "locked"]
ApprovalDecision = Literal["approved", "exception_approved"]


class ApprovalRecord(TypedDict, total=False):
    """PB 승인 수명주기 계약.

    UI/CLI는 reviewed까지만 기록하고 approval_gate만 locked로 전이한다.
    exception_approved는 severity=review 충돌에만 허용한다.
    """

    status: ApprovalStatus
    decision: ApprovalDecision
    approver: str
    note: str
    exception_reason: str
    reviewed_as_of: str
    locked_as_of: str
    unresolved_conflicts: list
    approval_hash: str
    created_as_of: str
    scope: str
    trade_approval: bool


class RiskState(TypedDict, total=False):
    run_config: dict
    demo_options: dict
    trace_id: str
    raw_input: str
    portfolio: list
    liquidity_required_krw: float | None
    market_data_ref: dict
    ips: dict
    ips_extraction_meta: dict
    conflicts: list
    conflict_policy: dict
    conflict_retries: int
    approval: ApprovalRecord
    metrics: dict
    explanations: list
    citations: list
    judge: dict
    judge_retries: int
    judge_feedback: str
    report: dict
