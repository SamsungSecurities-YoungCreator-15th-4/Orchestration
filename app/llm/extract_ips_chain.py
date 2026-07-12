"""고객 자연어 상담을 IPS 구조로 추출하는 LangChain Structured Output 체인."""
from __future__ import annotations

from typing import Literal

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.llm.client import get_llm
from app.state import (
    FIXED_AGE,
    FIXED_ASSET_EOK,
    FIXED_GOAL,
    FIXED_RISK,
    IPSProfile,
    UNIQUE_PREFIX,
)

MODEL_NAME = "gpt-4o"


class IPSExtractionDraft(BaseModel):
    """LLM 추출용 스키마. 고정값은 최종 IPSProfile 생성 시 재강제한다."""

    Name: str = "확인 필요"
    Job: str = "확인 필요"
    Return: float = Field(default=0.0, ge=0, description="목표 수익 금액, 억 원")
    Time: float = Field(default=0.0, ge=0, description="투자기간, 년")
    Tax: str = "확인 필요"
    Liquidity: Literal["낮음", "중간", "높음"] = "중간"
    Legal: str = "해당 사항 없음"
    Unique: str = ""
    liquidity_required_eok: float | None = Field(
        default=None,
        ge=0,
        description=(
            "상담에 명시된 유동성 필요 금액(억 원). 명시되지 않았다면 null이며 "
            "유동성 카테고리로 금액을 추측하지 않는다."
        ),
    )


PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """너는 PB 상담 기록에서 투자정책서(IPS) 필드를 추출하는 담당자다.
모델은 {model_name}이며 반드시 제공된 Structured Output 스키마로만 응답한다.
상담에 없는 텍스트는 '확인 필요', 없는 숫자는 0으로 둔다.
Return은 기대수익률(%)이 아니라 목표 수익 금액을 억 원 단위로 추출한다.
Liquidity는 낮음/중간/높음 중 하나로 분류한다.
liquidity_required_eok는 상담에 구체적 금액이 있을 때만 기록하고 추측하지 않는다.
Age={fixed_age}, Goal={fixed_goal}, Asset={fixed_asset}억, Risk={fixed_risk}는
시나리오 고정값이므로 상담 내용과 달라도 변경하지 않는다.
Unique에는 고객 특수 상황을 적되 '{unique_prefix}' 문구를 포함한다.""",
        ),
        ("human", "고객 상담 내용:\n{raw_input}"),
    ]
)


def build_extract_ips_chain(llm=None):
    """Azure OpenAI gpt-4o 기반 Structured Output 체인을 구성한다."""
    load_dotenv()
    model = llm or get_llm(temperature=0.0)
    return PROMPT | model.with_structured_output(IPSExtractionDraft)


def extract_ips_profile(raw_input: str, *, chain=None) -> tuple[IPSProfile, float | None]:
    """상담 내용을 추출하고 고정 시나리오 값을 결정론적으로 재적용한다."""
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise ValueError("고객 자연어 상담 입력이 비어 있습니다.")

    extractor = chain or build_extract_ips_chain()
    result = extractor.invoke(
        {
            "raw_input": raw_input.strip(),
            "model_name": MODEL_NAME,
            "fixed_age": FIXED_AGE,
            "fixed_goal": FIXED_GOAL,
            "fixed_asset": FIXED_ASSET_EOK,
            "fixed_risk": FIXED_RISK,
            "unique_prefix": UNIQUE_PREFIX,
        }
    )
    draft = result if isinstance(result, IPSExtractionDraft) else IPSExtractionDraft.model_validate(result)
    profile = IPSProfile(
        Name=draft.Name,
        Age=FIXED_AGE,
        Job=draft.Job,
        Goal=FIXED_GOAL,
        Asset=FIXED_ASSET_EOK,
        Return=draft.Return,
        Risk=FIXED_RISK,
        Time=draft.Time,
        Tax=draft.Tax,
        Liquidity=draft.Liquidity,
        Legal=draft.Legal,
        Unique=draft.Unique,
    )
    liquidity_required_krw = (
        draft.liquidity_required_eok * 100_000_000
        if draft.liquidity_required_eok is not None
        else None
    )
    return profile, liquidity_required_krw
