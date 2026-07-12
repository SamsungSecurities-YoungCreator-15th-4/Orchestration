"""고객 자연어 상담에서 IPS를 추출하는 LangChain Structured Output 노드."""
import os

from app.llm.extract_ips_chain import extract_ips_profile
from app.state import RiskState


def extract_ips(state: RiskState, *, chain=None) -> dict:
    profile, liquidity_required_krw = extract_ips_profile(
        state.get("raw_input") or "",
        chain=chain,
    )

    # 기존 CLI 충돌 시연 플래그를 유지한다(50억의 30%를 초과하는 20억).
    force_conflict = os.environ.get("RISK_FORCE_CONFLICT") == "1"
    if force_conflict:
        liquidity_required_krw = 2_000_000_000

    out: dict = {
        "ips": profile.model_dump(),
        "liquidity_required_krw": liquidity_required_krw,
    }
    # 충돌로 인한 재추출인 경우 재시도 횟수 기록
    if state.get("conflicts"):
        out["conflict_retries"] = (state.get("conflict_retries") or 0) + 1
    return out
