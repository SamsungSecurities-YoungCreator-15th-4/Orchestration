"""고객 자연어 상담을 IPS 구조로 추출하는 LangChain Structured Output 체인."""
from __future__ import annotations

import os
import re
from typing import Literal

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.llm.client import get_llm
from app.state import (
    FIXED_AGE,
    FIXED_ASSET_EOK,
    FIXED_GOAL,
    FIXED_JOB,
    FIXED_RISK,
    IPSProfile,
    UNIQUE_PREFIX,
)
from app.utils.hashing import sha256_of_dict

MODEL_NAME = "gpt-4o"
PROMPT_VERSION = "ips-extract-v2"
EXTRACTION_SEED = 42


class IPSExtractionDraft(BaseModel):
    """LLM 추출용 스키마. 고정값은 최종 IPSProfile 생성 시 재강제한다."""

    Name: str = "확인 필요"
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


SYSTEM_PROMPT = """너는 PB 상담 기록에서 투자정책서(IPS) 필드를 추출하는 담당자다.
모델은 {model_name}이며 반드시 제공된 Structured Output 스키마로만 응답한다.
상담에 없는 텍스트는 '확인 필요', 없는 숫자는 0으로 둔다.
Return은 기대수익률(%)이 아니라 목표 수익 금액을 억 원 단위로 추출한다.
수익률만 제시되고 목표 수익 금액이 없으면 Return=0으로 둔다.
Time은 년 단위로 환산한다(개월은 12로 나눈다).
Liquidity는 구체적 금액이 있으면 이를 최우선하고, 총자산 {fixed_asset}억 대비
10% 이하=낮음, 10% 초과 30% 이하=중간, 30% 초과=높음으로 분류한다.
liquidity_required_eok는 상담에 구체적 금액이 있을 때만 억 원으로 기록하고
유동성 카테고리에서 금액을 역산하거나 추측하지 않는다.
원 단위 금액은 정확히 100,000,000으로 나눠 억 원으로 바꾼다.
예: 300,000,000원=3억, 1,200,000,000원=12억이다.
Age={fixed_age}, Job={fixed_job}, Goal={fixed_goal}, Asset={fixed_asset}억,
Risk={fixed_risk}는 시나리오 고정값이므로 상담 내용과 달라도 변경하지 않는다.
Unique에는 고객 특수 상황을 간결하게 적는다. '{unique_prefix}' 문구는
후처리에서 강제되므로, 상담에서 확인한 추가 상황에 집중한다."""


PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", "고객 상담 내용:\n{raw_input}"),
    ]
)


def build_extract_ips_chain(llm=None):
    """Azure OpenAI gpt-4o 기반 Structured Output 체인을 구성한다."""
    load_dotenv()
    model = llm or get_llm(temperature=0.0, seed=EXTRACTION_SEED)
    return PROMPT | model.with_structured_output(IPSExtractionDraft, include_raw=True)


def _parse_chain_result(result) -> tuple[IPSExtractionDraft, dict]:
    """include_raw 결과와 테스트용 직접 dict 결과를 같은 계약으로 정규화한다."""
    if isinstance(result, dict) and "parsed" in result:
        if result.get("parsing_error") is not None:
            raise ValueError(f"IPS Structured Output 파싱 실패: {result['parsing_error']}")
        parsed = result.get("parsed")
        raw = result.get("raw")
        response_metadata = getattr(raw, "response_metadata", {}) or {}
        runtime = {
            "system_fingerprint": response_metadata.get("system_fingerprint"),
            "response_model": response_metadata.get("model_name"),
        }
    else:
        parsed = result
        runtime = {"system_fingerprint": None, "response_model": None}
    draft = (
        parsed
        if isinstance(parsed, IPSExtractionDraft)
        else IPSExtractionDraft.model_validate(parsed)
    )
    return draft, runtime


def _extraction_metadata(
    raw_input: str,
    profile: IPSProfile,
    liquidity_required_krw: float | None,
    runtime: dict,
) -> dict:
    output = {
        "ips": profile.model_dump(),
        "liquidity_required_krw": liquidity_required_krw,
    }
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "injected-test-chain")
    prompt_hash = sha256_of_dict(
        {
            "prompt_version": PROMPT_VERSION,
            "system_prompt": SYSTEM_PROMPT,
            "schema": IPSExtractionDraft.model_json_schema(),
        }
    )
    metadata = {
        "model": MODEL_NAME,
        "deployment": deployment,
        "api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        "temperature": 0.0,
        "seed": EXTRACTION_SEED,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "input_hash": sha256_of_dict({"raw_input": raw_input}),
        "output_hash": sha256_of_dict(output),
        **runtime,
    }
    metadata["extraction_hash"] = sha256_of_dict(metadata)
    return metadata


def _explicit_krw_amounts(raw_input: str) -> tuple[float | None, float | None]:
    """명시적인 큰 원화 금액의 단위 변환을 LLM 산술에 의존하지 않는다."""
    target_return = None
    liquidity = None
    for match in re.finditer(r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*원", raw_input):
        amount_krw = float(match.group("amount").replace(",", ""))
        if amount_krw < 10_000_000:
            continue
        context = raw_input[max(0, match.start() - 24) : min(len(raw_input), match.end() + 12)]
        amount_eok = amount_krw / 100_000_000
        if any(keyword in context for keyword in ("목표 수익", "수익금")):
            target_return = amount_eok
        elif any(
            keyword in context
            for keyword in ("유동성", "현금", "운영자금", "사업자금", "비상자금", "필요")
        ):
            liquidity = amount_eok
    return target_return, liquidity


def _liquidity_category(amount_eok: float | None, draft_value: str) -> str:
    if amount_eok is None:
        return draft_value
    ratio = amount_eok / FIXED_ASSET_EOK
    if ratio <= 0.10:
        return "낮음"
    if ratio <= 0.30:
        return "중간"
    return "높음"


def extract_ips_profile_with_meta(
    raw_input: str, *, chain=None
) -> tuple[IPSProfile, float | None, dict]:
    """상담 내용을 추출하고 실행 재현성 메타데이터까지 반환한다."""
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise ValueError("고객 자연어 상담 입력이 비어 있습니다.")

    normalized_input = raw_input.strip()
    extractor = chain or build_extract_ips_chain()
    result = extractor.invoke(
        {
            "raw_input": normalized_input,
            "model_name": MODEL_NAME,
            "fixed_age": FIXED_AGE,
            "fixed_job": FIXED_JOB,
            "fixed_goal": FIXED_GOAL,
            "fixed_asset": FIXED_ASSET_EOK,
            "fixed_risk": FIXED_RISK,
            "unique_prefix": UNIQUE_PREFIX,
        }
    )
    draft, runtime = _parse_chain_result(result)
    explicit_return, explicit_liquidity = _explicit_krw_amounts(normalized_input)
    return_eok = explicit_return if explicit_return is not None else draft.Return
    liquidity_eok = (
        explicit_liquidity
        if explicit_liquidity is not None
        else draft.liquidity_required_eok
    )
    profile = IPSProfile(
        Name=draft.Name.strip() or "확인 필요",
        Age=FIXED_AGE,
        Job=FIXED_JOB,
        Goal=FIXED_GOAL,
        Asset=FIXED_ASSET_EOK,
        Return=return_eok,
        Risk=FIXED_RISK,
        Time=draft.Time,
        Tax=draft.Tax.strip() or "확인 필요",
        Liquidity=_liquidity_category(liquidity_eok, draft.Liquidity),
        Legal=draft.Legal.strip() or "해당 사항 없음",
        Unique=draft.Unique.strip(),
    )
    liquidity_required_krw = (
        liquidity_eok * 100_000_000
        if liquidity_eok is not None
        else None
    )
    metadata = _extraction_metadata(
        normalized_input,
        profile,
        liquidity_required_krw,
        runtime,
    )
    return profile, liquidity_required_krw, metadata


def extract_ips_profile(raw_input: str, *, chain=None) -> tuple[IPSProfile, float | None]:
    """기존 호출자용 공개 API. 재현성 메타데이터는 with_meta API에서 제공한다."""
    profile, liquidity_required_krw, _metadata = extract_ips_profile_with_meta(
        raw_input,
        chain=chain,
    )
    return profile, liquidity_required_krw
