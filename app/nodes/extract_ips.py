"""고객 자연어 상담에서 IPS를 추출하는 LangChain Structured Output 노드."""
import os

from app.llm.extract_ips_chain import extract_ips_profile
from app.state import IPSProfile, RiskState


def _offline_profile() -> tuple[IPSProfile, float]:
    """외부 키 없는 CI graph smoke용 결정론 IPS 입력."""
    return (
        IPSProfile(
            Name="시연 고객",
            Job="자영업자",
            Return=5.0,
            Time=10.0,
            Tax="금융소득종합과세 대상 여부 확인 필요",
            Liquidity="중간",
            Legal="해당 사항 없음",
            Unique="자영업 사업소득 변동성 존재",
        ),
        500_000_000,
    )


def extract_ips(state: RiskState, *, chain=None) -> dict:
    demo_options = state.get("demo_options") or {}
    if demo_options.get("offline") is True:
        profile, liquidity_required_krw = _offline_profile()
    else:
        profile, liquidity_required_krw = extract_ips_profile(
            state.get("raw_input") or "",
            chain=chain,
        )

    # UI/CLI 세션별 충돌 시연(50억의 30%를 초과하는 20억).
    # 환경변수는 이전 호출 방식과의 하위 호환용으로만 읽는다.
    force_conflict = demo_options.get("force_conflict") is True
    if "force_conflict" not in demo_options:
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
