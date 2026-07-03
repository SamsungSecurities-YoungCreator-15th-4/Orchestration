from typing import TypedDict, Literal
from pydantic import BaseModel

class IPSProfile(BaseModel):
    return_target_pct: float | None = None
    risk_tolerance: Literal["conservative", "neutral", "aggressive"] = "neutral"
    time_horizon_years: float | None = None
    tax_notes: list[str] = []
    liquidity_needs: list[dict] = []
    legal_constraints: list[str] = []
    unique_circumstances: list[str] = []
    evidence: list[dict] = []

class RiskState(TypedDict, total=False):
    run_config: dict
    trace_id: str
    raw_input: str
    portfolio: list
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
