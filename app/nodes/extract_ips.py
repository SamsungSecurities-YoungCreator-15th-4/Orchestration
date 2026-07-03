"""IPS(투자정책서) 프로필 추출 — 현재는 고정 스텁.

TODO: LangChain structured output(with_structured_output(IPSProfile))으로
raw_input에서 실제 추출하도록 연결.
"""
import os

from app.state import IPSProfile, RiskState


def extract_ips(state: RiskState) -> dict:
    # 시연용 강제 충돌: 유동성 요구를 위탁자산 30% 초과로 과대 설정
    force_conflict = os.environ.get("RISK_FORCE_CONFLICT") == "1"

    liquidity_needs = (
        [
            {"purpose": "사업 운영자금", "amount_krw": 1_200_000_000, "when": "6개월 내"},
            {"purpose": "부동산 계약금", "amount_krw": 800_000_000, "when": "1년 내"},
        ]
        if force_conflict
        else [
            {"purpose": "사업 운영자금", "amount_krw": 500_000_000, "when": "1년 내"},
        ]
    )

    profile = IPSProfile(
        return_target_pct=5.5,
        risk_tolerance="neutral",
        time_horizon_years=10.0,
        tax_notes=["금융소득종합과세 대상 여부 확인 필요"],
        liquidity_needs=liquidity_needs,
        legal_constraints=[],
        unique_circumstances=["자영업 사업소득 변동성 존재"],
        evidence=[{"source": "raw_input", "quote": "50대 자영업자, 위탁자산 50억 원"}],
    )

    out: dict = {"ips": profile.model_dump()}
    # 충돌로 인한 재추출인 경우 재시도 횟수 기록
    if state.get("conflicts"):
        out["conflict_retries"] = (state.get("conflict_retries") or 0) + 1
    return out
