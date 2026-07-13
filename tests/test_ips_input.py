"""IPS Structured Output·포트폴리오 입력·충돌 연동 테스트."""
import pytest
from pydantic import ValidationError

from app.llm.extract_ips_chain import extract_ips_profile
from app.nodes.conflict_check import conflict_check
from app.nodes.extract_ips import extract_ips
from app.nodes.load_inputs import (
    ASSET_DEFINITIONS,
    SAMPLE_RAW_INPUT,
    load_inputs,
    portfolio_from_percentages,
)
from app.state import FIXED_GOAL, FIXED_JOB, IPSProfile, UNIQUE_PREFIX


class FakeStructuredChain:
    def invoke(self, values):
        assert values["raw_input"]
        return {
            "Name": "홍길동",
            "Job": "카페 운영 자영업자",
            "Return": 4.0,
            "Time": 8.0,
            "Tax": "금융소득종합과세 확인 필요",
            "Liquidity": "높음",
            "Legal": "해당 사항 없음",
            "Unique": "사업소득 변동성이 큼",
            "liquidity_required_eok": 20.0,
        }


def test_ips_fixed_fields_and_unique_prefix_are_enforced():
    profile, liquidity_krw = extract_ips_profile(
        "고객 상담",
        chain=FakeStructuredChain(),
    )

    result = profile.model_dump()
    assert list(result) == [
        "Name", "Age", "Job", "Goal", "Asset", "Return",
        "Risk", "Time", "Tax", "Liquidity", "Legal", "Unique",
    ]
    assert result["Age"] == "50"
    assert result["Job"] == FIXED_JOB
    assert result["Goal"] == FIXED_GOAL
    assert result["Asset"] == 50.0
    assert result["Risk"] == "균형형"
    assert result["Unique"].startswith(UNIQUE_PREFIX)
    assert liquidity_krw == 2_000_000_000


def test_fixed_ips_values_cannot_be_changed():
    with pytest.raises(ValidationError):
        IPSProfile(Age="65")
    with pytest.raises(ValidationError):
        IPSProfile(Risk="공격형")
    with pytest.raises(ValidationError):
        IPSProfile(Job="회사원")


def test_extract_node_stores_public_ips_and_internal_liquidity_amount(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_CONFLICT", raising=False)
    result = extract_ips({"raw_input": "고객 상담"}, chain=FakeStructuredChain())

    assert "liquidity_required_eok" not in result["ips"]
    assert result["liquidity_required_krw"] == 2_000_000_000


def test_extract_node_offline_mode_does_not_require_llm(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("offline mode must not call the LLM")

    monkeypatch.setattr(
        "app.nodes.extract_ips.extract_ips_profile_with_meta", fail_if_called
    )
    result = extract_ips(
        {"raw_input": "고객 상담", "demo_options": {"offline": True}}
    )

    assert result["ips"]["Job"] == "자영업자"
    assert result["liquidity_required_krw"] == 250_000_000


def test_extract_conflict_demo_option_is_isolated_in_state(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_CONFLICT", raising=False)
    result = extract_ips(
        {
            "raw_input": "고객 상담",
            "demo_options": {"force_conflict": True},
        },
        chain=FakeStructuredChain(),
    )

    assert result["liquidity_required_krw"] == 2_000_000_000


def test_new_ips_liquidity_amount_uses_existing_30_percent_conflict_rule():
    portfolio = portfolio_from_percentages(
        {asset_class: value for (asset_class, _), value in zip(ASSET_DEFINITIONS, [25, 20, 25, 15, 10, 5])}
    )
    result = conflict_check(
        {
            "ips": {"Liquidity": "높음"},
            "portfolio": portfolio,
            "liquidity_required_krw": 2_000_000_000,
        }
    )

    over_limit = next(
        conflict
        for conflict in result["conflicts"]
        if conflict["rule"] == "liquidity_over_30pct"
    )
    assert over_limit["severity"] == "review"
    assert over_limit["limit_krw"] == 1_500_000_000
    assert result["conflict_policy"]["policy_hash"]


def test_conflict_check_blocks_missing_time_and_allows_review_for_concentration():
    portfolio = portfolio_from_percentages(
        {
            asset_class: value
            for (asset_class, _), value in zip(
                ASSET_DEFINITIONS,
                [70, 0, 10, 10, 5, 5],
            )
        }
    )
    result = conflict_check(
        {
            "ips": {"Time": 0, "Risk": "균형형", "Liquidity": "낮음"},
            "portfolio": portfolio,
            "liquidity_required_krw": 0,
        }
    )
    by_rule = {conflict["rule"]: conflict for conflict in result["conflicts"]}
    assert by_rule["time_horizon_missing"]["severity"] == "block"
    assert by_rule["time_horizon_missing"]["exception_allowed"] is False
    assert by_rule["balanced_risky_assets_over_limit"]["severity"] == "review"
    assert by_rule["single_risky_asset_concentration"]["exception_allowed"] is True


def test_portfolio_percentages_convert_to_50_eok_contract():
    percentages = {
        asset_class: value
        for (asset_class, _), value in zip(ASSET_DEFINITIONS, [25, 20, 25, 15, 10, 5])
    }
    portfolio = portfolio_from_percentages(percentages)

    assert sum(item["value_krw"] for item in portfolio) == 5_000_000_000
    assert sum(item["weight"] for item in portfolio) == pytest.approx(1.0)


def test_portfolio_percentages_must_sum_to_100():
    percentages = {asset_class: 10.0 for asset_class, _ in ASSET_DEFINITIONS}
    with pytest.raises(ValueError, match="100%"):
        portfolio_from_percentages(percentages)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_portfolio_percentages_reject_non_finite_values(invalid):
    percentages = {
        asset_class: value
        for (asset_class, _), value in zip(
            ASSET_DEFINITIONS,
            [25, 20, 25, 15, 10, 5],
        )
    }
    percentages["cash"] = invalid

    with pytest.raises(ValueError, match="유효한 숫자"):
        portfolio_from_percentages(percentages)


def test_load_inputs_preserves_ui_raw_input_and_portfolio(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        'seed: 42\nas_of_date: "2026-07-03"\ndata_source: dummy\n',
        encoding="utf-8",
    )
    import app.nodes.load_inputs as load_inputs_module

    monkeypatch.setattr(load_inputs_module, "CONFIG_PATH", config)
    portfolio = portfolio_from_percentages(
        {asset_class: value for (asset_class, _), value in zip(ASSET_DEFINITIONS, [20, 20, 20, 20, 10, 10])}
    )
    result = load_inputs({"raw_input": "UI 상담 입력", "portfolio": portfolio})

    assert result["raw_input"] == "UI 상담 입력"
    assert result["portfolio"] == portfolio
    assert result["raw_input"] != SAMPLE_RAW_INPUT


def test_load_inputs_does_not_replace_explicit_empty_consultation(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        'seed: 42\nas_of_date: "2026-07-03"\ndata_source: dummy\n',
        encoding="utf-8",
    )
    import app.nodes.load_inputs as load_inputs_module

    monkeypatch.setattr(load_inputs_module, "CONFIG_PATH", config)
    result = load_inputs({"raw_input": ""})

    assert result["raw_input"] == ""


def test_load_inputs_offline_mode_selects_dummy_data(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        'seed: 42\nas_of_date: "2026-07-03"\ndata_source: real\n',
        encoding="utf-8",
    )
    import app.nodes.load_inputs as load_inputs_module

    monkeypatch.setattr(load_inputs_module, "CONFIG_PATH", config)
    result = load_inputs({"demo_options": {"offline": True}})

    assert result["run_config"]["data_source"] == "dummy"
    assert result["market_data_ref"]["source"] == "dummy"
