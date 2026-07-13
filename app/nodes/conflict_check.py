"""공식 적합성 원칙 + 내부 정량 임계값에 기반한 IPS 사전 충돌 검사."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.state import RiskState
from app.utils.hashing import sha256_of_dict

POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "ips_policy.yaml"
RISKY_ASSET_CLASSES = {"domestic_equity", "global_equity", "alternatives"}


@lru_cache(maxsize=1)
def _load_policy() -> dict:
    """프로세스 수명 동안 버전된 정적 정책을 한 번만 읽는다."""
    with open(POLICY_PATH, encoding="utf-8") as file:
        policy = yaml.safe_load(file)
    if not isinstance(policy, dict) or not policy.get("version"):
        raise ValueError("IPS 충돌 정책에 version이 필요합니다.")
    return policy


def _conflict(
    *,
    policy: dict,
    rule: str,
    severity: str,
    category: str,
    detail: str,
    observed,
    limit,
    unit: str,
    evidence_refs: list[str],
    extra: dict | None = None,
) -> dict:
    item = {
        "rule": rule,
        "severity": severity,
        "exception_allowed": severity == "review",
        "category": category,
        "detail": detail,
        "observed": observed,
        "limit": limit,
        "unit": unit,
        "policy_version": policy["version"],
        "policy_ref": policy["policy_ref"],
        "evidence_refs": evidence_refs,
    }
    if extra:
        item.update(extra)
    return item


def _portfolio_values(portfolio: list) -> tuple[float, dict[str, float]]:
    values: dict[str, float] = {}
    total = 0.0
    for item in portfolio:
        if not isinstance(item, dict):
            continue
        value = item.get("value_krw")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            asset_class = str(item.get("asset_class") or "unknown")
            values[asset_class] = values.get(asset_class, 0.0) + float(value)
            total += float(value)
    return total, values


def conflict_check(state: RiskState) -> dict:
    """계산 전 적합성 red flag를 block/review 두 단계로 반환한다.

    block은 정보·자산 계약 자체가 불완전해 PB도 예외 승인할 수 없다.
    review는 내부 보수적 임계값으로, PB가 사유를 남긴 경우에만 계산 가능하다.
    """
    policy = _load_policy()
    thresholds = policy["thresholds"]
    ips = state.get("ips") or {}
    portfolio = state.get("portfolio") or []
    total_value, values = _portfolio_values(portfolio)
    conflicts: list[dict] = []

    policy_meta = {
        "version": policy["version"],
        "policy_ref": policy["policy_ref"],
        "policy_hash": sha256_of_dict(policy),
        "source_ids": [source["id"] for source in policy.get("sources", [])],
    }

    if total_value <= 0:
        conflicts.append(
            _conflict(
                policy=policy,
                rule="portfolio_value_missing",
                severity="block",
                category="data_quality",
                detail="제안 포트폴리오의 유효한 평가금액이 없어 적합성 검사를 수행할 수 없음",
                observed=total_value,
                limit=0,
                unit="KRW",
                evidence_refs=["FCPA_ART17", "FINRA_2111"],
            )
        )
        return {"conflicts": conflicts, "conflict_policy": policy_meta}

    time_horizon = ips.get("Time")
    if not isinstance(time_horizon, (int, float)) or isinstance(time_horizon, bool) or time_horizon <= 0:
        conflicts.append(
            _conflict(
                policy=policy,
                rule="time_horizon_missing",
                severity="block",
                category="ips_completeness",
                detail="투자기간이 확인되지 않아 고객별 적합성 판단을 완료할 수 없음",
                observed=time_horizon,
                limit="> 0",
                unit="years",
                evidence_refs=["KOFIA_STANDARD", "FINRA_2111"],
            )
        )

    extracted_amount = state.get("liquidity_required_krw")
    if extracted_amount is None:
        legacy_needs = ips.get("liquidity_needs") or []
        liquidity_total = sum(
            need.get("amount_krw") or 0
            for need in legacy_needs
            if isinstance(need, dict)
        )
    else:
        liquidity_total = float(extracted_amount)

    if liquidity_total > total_value:
        conflicts.append(
            _conflict(
                policy=policy,
                rule="liquidity_exceeds_portfolio",
                severity="block",
                category="liquidity",
                detail=(
                    f"유동성 필요금액 {liquidity_total:,.0f}원이 위탁자산 "
                    f"{total_value:,.0f}원을 초과"
                ),
                observed=liquidity_total,
                limit=total_value,
                unit="KRW",
                evidence_refs=["FINRA_2111", "KOFIA_STANDARD"],
            )
        )
    elif liquidity_total > 0:
        cash_value = values.get("cash", 0.0)
        if liquidity_total > cash_value:
            conflicts.append(
                _conflict(
                    policy=policy,
                    rule="liquidity_cash_shortfall",
                    severity="review",
                    category="liquidity",
                    detail=(
                        f"유동성 필요금액 {liquidity_total:,.0f}원이 현금성자산 "
                        f"{cash_value:,.0f}원을 초과"
                    ),
                    observed=liquidity_total,
                    limit=cash_value,
                    unit="KRW",
                    evidence_refs=["FINRA_2111"],
                    extra={
                        "liquidity_total_krw": liquidity_total,
                        "cash_value_krw": cash_value,
                    },
                )
            )

        liquidity_limit_ratio = thresholds["max_liquidity_need_ratio"]
        liquidity_limit = total_value * liquidity_limit_ratio
        if liquidity_total > liquidity_limit:
            conflicts.append(
                _conflict(
                    policy=policy,
                    rule="liquidity_over_30pct",
                    severity="review",
                    category="liquidity",
                    detail=(
                        f"유동성 요구 합계 {liquidity_total:,.0f}원이 위탁자산 "
                        f"{total_value:,.0f}원의 {liquidity_limit_ratio:.0%}"
                        f"({liquidity_limit:,.0f}원)를 초과"
                    ),
                    observed=liquidity_total / total_value,
                    limit=liquidity_limit_ratio,
                    unit="ratio",
                    evidence_refs=["FINRA_2111", "INTERNAL_POLICY"],
                    extra={
                        "liquidity_total_krw": liquidity_total,
                        "limit_krw": liquidity_limit,
                    },
                )
            )

    risky_value = sum(values.get(asset, 0.0) for asset in RISKY_ASSET_CLASSES)
    risky_ratio = risky_value / total_value
    if (
        isinstance(time_horizon, (int, float))
        and not isinstance(time_horizon, bool)
        and 0 < time_horizon < thresholds["min_years_for_risky_assets"]
        and risky_value > 0
    ):
        conflicts.append(
            _conflict(
                policy=policy,
                rule="short_horizon_risky_assets",
                severity="review",
                category="time_horizon",
                detail=(
                    f"투자기간 {time_horizon:g}년이 위험자산 검토 기준 "
                    f"{thresholds['min_years_for_risky_assets']:g}년보다 짧지만 "
                    f"위험자산 비중이 {risky_ratio:.1%}임"
                ),
                observed=time_horizon,
                limit=thresholds["min_years_for_risky_assets"],
                unit="years",
                evidence_refs=["KOFIA_STANDARD", "INVESTOR_GOV_ALLOCATION"],
            )
        )

    if ips.get("Risk") == "균형형" and risky_ratio > thresholds["balanced_max_risky_ratio"]:
        conflicts.append(
            _conflict(
                policy=policy,
                rule="balanced_risky_assets_over_limit",
                severity="review",
                category="risk_tolerance",
                detail=(
                    f"균형형 고객의 주식·대체투자 비중 {risky_ratio:.1%}가 내부 "
                    f"사전검토 기준 {thresholds['balanced_max_risky_ratio']:.0%}를 초과"
                ),
                observed=risky_ratio,
                limit=thresholds["balanced_max_risky_ratio"],
                unit="ratio",
                evidence_refs=["KOFIA_STANDARD", "INVESTOR_GOV_ALLOCATION", "INTERNAL_POLICY"],
            )
        )

    concentration_limit = thresholds["max_single_risky_asset_ratio"]
    for asset_class in sorted(RISKY_ASSET_CLASSES):
        asset_ratio = values.get(asset_class, 0.0) / total_value
        if asset_ratio > concentration_limit:
            conflicts.append(
                _conflict(
                    policy=policy,
                    rule="single_risky_asset_concentration",
                    severity="review",
                    category="concentration",
                    detail=(
                        f"{asset_class} 비중 {asset_ratio:.1%}가 단일 위험자산 내부 "
                        f"검토 기준 {concentration_limit:.0%}를 초과"
                    ),
                    observed=asset_ratio,
                    limit=concentration_limit,
                    unit="ratio",
                    evidence_refs=["INVESTOR_GOV_DIVERSIFICATION", "INTERNAL_POLICY"],
                    extra={"asset_class": asset_class},
                )
            )

    if ips.get("Liquidity") == "높음" and extracted_amount is None:
        conflicts.append(
            _conflict(
                policy=policy,
                rule="high_liquidity_amount_missing",
                severity="block",
                category="ips_completeness",
                detail="유동성 등급은 높음이지만 필요금액이 확인되지 않아 충당 가능성을 검증할 수 없음",
                observed=None,
                limit="명시적 금액 필요",
                unit="KRW",
                evidence_refs=["FINRA_2111", "KOFIA_STANDARD"],
            )
        )

    return {"conflicts": conflicts, "conflict_policy": policy_meta}
