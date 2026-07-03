"""결정론 계층 — 스트레스 시나리오 계산.

주의: 이 패키지(app.engine)에서는 langchain/llm 관련 import 금지.
"""

# 고금리·강달러 시나리오: 자산군별 고정 충격률 (결정론적)
SCENARIO_HIGH_RATE_STRONG_USD = {
    "name": "high_rate_strong_usd",
    "description": "기준금리 +200bp, 원/달러 +10% 복합 충격",
    "shocks": {
        "domestic_equity": -0.12,
        "global_equity": -0.06,
        "domestic_bond": -0.05,
        "global_bond": -0.01,
        "alternatives": -0.08,
        "cash": 0.0,
    },
}


def run_stress(portfolio: list[dict], scenario: dict | None = None) -> dict:
    """자산군별 고정 충격을 적용해 포트폴리오 손실액/손실률 계산."""
    scenario = scenario or SCENARIO_HIGH_RATE_STRONG_USD
    total_value = sum(p["value_krw"] for p in portfolio)
    loss = 0.0
    by_asset = {}
    for p in portfolio:
        shock = scenario["shocks"].get(p["asset_class"], 0.0)
        asset_loss = p["value_krw"] * shock
        by_asset[p["asset_class"]] = round(asset_loss, 2)
        loss += asset_loss
    return {
        "scenario": scenario["name"],
        "description": scenario["description"],
        "loss_krw": round(loss, 2),
        "loss_pct": round(loss / total_value, 8) if total_value else 0.0,
        "by_asset": by_asset,
    }
