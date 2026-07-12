"""룰 기반 IPS 충돌 검사 (실제 구현).

룰: 유동성 요구 합계가 위탁자산의 30%를 초과하면 충돌로 판정.
"""
from app.state import RiskState

LIQUIDITY_LIMIT_RATIO = 0.30


def conflict_check(state: RiskState) -> dict:
    ips = state.get("ips") or {}
    portfolio = state.get("portfolio") or []
    total_value = sum(p["value_krw"] for p in portfolio)

    # 새 IPS 공개 JSON은 Liquidity 카테고리만 노출한다. 30% 충돌 판정에 필요한
    # 구체적 금액은 추출 노드가 별도 상태값으로 저장한다. 이전 IPS 계약의
    # liquidity_needs도 계속 지원해 기존 실행·테스트와 호환한다.
    extracted_amount = state.get("liquidity_required_krw")
    liquidity_total = (
        extracted_amount
        if extracted_amount is not None
        else sum(n.get("amount_krw", 0) for n in (ips.get("liquidity_needs") or []))
    )

    conflicts = []
    if total_value and liquidity_total > total_value * LIQUIDITY_LIMIT_RATIO:
        conflicts.append(
            {
                "rule": "liquidity_over_30pct",
                "detail": (
                    f"유동성 요구 합계 {liquidity_total:,.0f}원이 "
                    f"위탁자산 {total_value:,.0f}원의 30%"
                    f"({total_value * LIQUIDITY_LIMIT_RATIO:,.0f}원)를 초과"
                ),
                "liquidity_total_krw": liquidity_total,
                "limit_krw": total_value * LIQUIDITY_LIMIT_RATIO,
            }
        )

    return {"conflicts": conflicts}
