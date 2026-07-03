"""룰 기반 IPS 충돌 검사 (실제 구현).

룰: 유동성 요구 합계가 위탁자산의 30%를 초과하면 충돌로 판정.
"""
from app.state import RiskState

LIQUIDITY_LIMIT_RATIO = 0.30


def conflict_check(state: RiskState) -> dict:
    ips = state.get("ips", {})
    portfolio = state.get("portfolio", [])
    total_value = sum(p["value_krw"] for p in portfolio)

    liquidity_total = sum(
        n.get("amount_krw", 0) for n in ips.get("liquidity_needs", [])
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
