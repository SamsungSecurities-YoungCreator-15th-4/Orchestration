"""결정론 계층 — 스트레스 시나리오 계산.

주의: 이 패키지(app.engine)에서는 langchain/llm 관련 import 금지.
"""

# 스트레스 시나리오는 사전 정의·문서화되며, 실행 시점에 임의로 생성되지 않는다.
# 충격 크기는 역사적 사례를 참조한 초안 제안값이며(reference 병기), 최종 강도는
# 회의에서 확정한다. 자산군별 변환 규칙(shocks)은 결정론적으로 고정된다.

# 시나리오 A — 고금리 충격: 정책금리 급등 국면.
# 금리 상승 → 채권 가격 직접 하락(듀레이션 효과) + 주식·대체 할인율 상승 충격.
SCENARIO_A_HIGH_RATE = {
    "name": "A_high_rate",
    "description": "정책금리 +250bp 급등 — 채권 가격 직접 하락 + 주식·대체 할인율 상승 충격",
    "reference": "2022년 고금리 국면(한·미 정책금리 급등) 참조 — 초안 제안값",
    "shocks": {
        "domestic_equity": -0.12,
        "global_equity": -0.08,
        "domestic_bond": -0.06,
        "global_bond": -0.04,
        "alternatives": -0.07,
        "cash": 0.0,
    },
}

# 시나리오 B — 강달러 충격: 원/달러 급등 국면.
# 미헤지 외화자산은 FX 환산이익이 위험회피 손실을 일부 상쇄(순손실 유지),
# 원화자산은 자본유출·위험회피 충격을 직접 받는다.
SCENARIO_B_STRONG_USD = {
    "name": "B_strong_usd",
    "description": "원/달러 +10% 급등 — 미헤지 외화자산 FX 환산이익이 위험회피 손실을 일부 상쇄, 원화자산은 위험회피 직격",
    "reference": "2022년 강달러 국면(원/달러 1,440원대) 참조 — 초안 제안값",
    "shocks": {
        "domestic_equity": -0.09,
        "global_equity": -0.03,
        "domestic_bond": -0.03,
        "global_bond": -0.01,
        "alternatives": -0.05,
        "cash": 0.0,
    },
}

# 기본 시나리오 세트(순서 고정) — 리포트에 A·B 나란히 표기.
DEFAULT_SCENARIOS = [SCENARIO_A_HIGH_RATE, SCENARIO_B_STRONG_USD]


def run_stress(portfolio: list[dict], scenario: dict | None = None) -> dict:
    """단일 시나리오의 자산군별 고정 충격을 적용해 포트폴리오 손실액/손실률 계산.

    부호 규약: loss_krw·loss_pct는 **양수 = 손실**(historical_var 규약과 통일).
    소비자(assemble_report)가 abs()로 부호를 뒤집을 필요가 없다.
    """
    scenario = scenario or SCENARIO_A_HIGH_RATE
    shocks = scenario["shocks"]

    # 시나리오에 충격이 정의되지 않은 자산군은 조용히 0으로 통과하면 리스크가
    # 과소평가된다(portfolio_returns와 동일한 방어). cash: 0.0처럼 '의도된 0'은
    # 시나리오에 명시돼 있으므로 이 검증에 걸리지 않는다.
    unknown = {p["asset_class"] for p in portfolio} - shocks.keys()
    if unknown:
        raise ValueError(
            f"시나리오 {scenario['name']}에 충격이 정의되지 않은 자산군입니다: {sorted(unknown)}"
        )

    total_value = sum(p["value_krw"] for p in portfolio)
    loss = 0.0  # 양수 = 손실
    # 같은 자산군이 여러 종목으로 들어와도 덮어쓰지 않고 합산한다.
    by_asset: dict[str, float] = {}
    for p in portfolio:
        asset_class = p["asset_class"]
        # shock이 음수(하락)면 손실은 양수가 된다.
        asset_loss = -(p["value_krw"] * shocks[asset_class])
        by_asset[asset_class] = by_asset.get(asset_class, 0.0) + asset_loss
        loss += asset_loss
    return {
        "scenario": scenario["name"],
        "description": scenario["description"],
        "reference": scenario.get("reference"),
        "loss_krw": round(loss, 2),
        "loss_pct": round(loss / total_value, 8) if total_value else 0.0,
        "by_asset": {k: round(v, 2) for k, v in by_asset.items()},
    }


def run_all_stress(
    portfolio: list[dict], scenarios: list[dict] | None = None
) -> dict:
    """기본 시나리오 세트(A 고금리 · B 강달러)를 모두 적용해 결과를 나란히 반환.

    동일 포트폴리오·동일 시나리오 정의면 결과는 항상 동일하게 재현된다.
    """
    scenarios = scenarios or DEFAULT_SCENARIOS
    return {s["name"]: run_stress(portfolio, s) for s in scenarios}
