"""load_inputs 노드 — market_data_ref가 실제 data_source와 일치하는지 검증.

[리뷰 반영] market_data_ref["source"]가 항상 "dummy"로 하드코딩돼 있어,
var_engine이 실제로는 data_source="real"을 쓰는데 state에는 "dummy"로
남는 출처 모순이 있었다. config.yaml의 data_source를 그대로 반영하도록
고친 뒤, 그 회귀를 여기서 검증한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nodes.load_inputs import load_inputs


def test_market_data_ref_matches_config_data_source():
    """config.yaml의 data_source(현재 "real")가 market_data_ref.source에 그대로 반영된다."""
    result = load_inputs({})
    assert result["market_data_ref"]["source"] == "real"
    assert "yfinance" in result["market_data_ref"]["note"]


def test_market_data_ref_reflects_dummy_source(monkeypatch, tmp_path):
    """data_source="dummy"인 config에서는 market_data_ref도 dummy로 일관되게 남는다."""
    import app.nodes.load_inputs as load_inputs_mod

    dummy_config_path = tmp_path / "config.yaml"
    dummy_config_path.write_text(
        'seed: 42\nas_of_date: "2026-07-03"\nbase_currency: KRW\n'
        "rf_rate: 0.0325\nvar_confidence: 0.99\nhorizons: [1, 10]\n"
        "data_source: dummy\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(load_inputs_mod, "CONFIG_PATH", dummy_config_path)

    result = load_inputs({})
    assert result["market_data_ref"]["source"] == "dummy"
    assert "더미" in result["market_data_ref"]["note"]
