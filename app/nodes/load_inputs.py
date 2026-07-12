"""config.yaml 로드 + 고객 상담/제안 포트폴리오 입력 정규화."""
from pathlib import Path

import yaml

from app.state import RiskState
from app.utils.hashing import sha256_of_dict

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# 6자산군 더미 포트폴리오 — 위탁자산 총 50억 원 (고정)
DUMMY_PORTFOLIO = [
    {"asset_class": "domestic_equity", "name": "국내주식", "value_krw": 1_250_000_000, "weight": 0.25},
    {"asset_class": "global_equity", "name": "해외주식", "value_krw": 1_000_000_000, "weight": 0.20},
    {"asset_class": "domestic_bond", "name": "국내채권", "value_krw": 1_250_000_000, "weight": 0.25},
    {"asset_class": "global_bond", "name": "해외채권", "value_krw": 750_000_000, "weight": 0.15},
    {"asset_class": "alternatives", "name": "대체투자", "value_krw": 500_000_000, "weight": 0.10},
    {"asset_class": "cash", "name": "현금성자산", "value_krw": 250_000_000, "weight": 0.05},
]

ASSET_DEFINITIONS = [
    ("domestic_equity", "국내주식"),
    ("global_equity", "해외주식"),
    ("domestic_bond", "국내채권"),
    ("global_bond", "해외채권"),
    ("alternatives", "대체투자"),
    ("cash", "현금성자산"),
]

TOTAL_ASSET_KRW = 5_000_000_000

SAMPLE_RAW_INPUT = (
    "고객: 50대 자영업자, 위탁자산 50억 원. 위험성향은 중립적이며, "
    "은퇴까지 약 10년의 투자기간을 상정한다. 최근 고금리·강달러 환경이 "
    "지속되는 상황에서 포트폴리오의 하방 리스크를 점검하고자 한다. "
    "사업 운영상 일부 유동성 확보가 필요할 수 있다."
)


def portfolio_from_percentages(
    percentages: dict[str, float],
    *,
    total_asset_krw: float = TOTAL_ASSET_KRW,
) -> list[dict]:
    """6자산 비중(%)을 정량 엔진의 금액·0~1 비중 계약으로 변환한다."""
    expected = {asset_class for asset_class, _name in ASSET_DEFINITIONS}
    if set(percentages) != expected:
        missing = sorted(expected - set(percentages))
        extra = sorted(set(percentages) - expected)
        raise ValueError(f"포트폴리오 자산군 불일치: missing={missing}, extra={extra}")

    normalized = {key: float(value) for key, value in percentages.items()}
    if any(value < 0 or value > 100 for value in normalized.values()):
        raise ValueError("자산별 비중은 0% 이상 100% 이하여야 합니다.")
    total_pct = sum(normalized.values())
    if abs(total_pct - 100.0) > 1e-6:
        raise ValueError(f"포트폴리오 비중 합계는 100%여야 합니다: 현재 {total_pct:g}%")

    return [
        {
            "asset_class": asset_class,
            "name": name,
            "value_krw": round(total_asset_krw * normalized[asset_class] / 100.0, 2),
            "weight": normalized[asset_class] / 100.0,
        }
        for asset_class, name in ASSET_DEFINITIONS
    ]


def load_inputs(state: RiskState) -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    run_config = dict(config)
    run_config["config_hash"] = sha256_of_dict(config)

    raw_input = state["raw_input"] if "raw_input" in state else SAMPLE_RAW_INPUT
    portfolio = state["portfolio"] if "portfolio" in state else DUMMY_PORTFOLIO

    return {
        "run_config": run_config,
        "trace_id": f"run-{run_config['config_hash'][:12]}",
        "raw_input": raw_input,
        "portfolio": [dict(item) for item in portfolio],
        "market_data_ref": {
            "source": config.get("data_source", "real"),
            "as_of_date": config["as_of_date"],
            "note": (
                "yfinance 실데이터(app.engine.returns.load_real_returns) 연동"
                if config.get("data_source", "real") == "real"
                else "고정 수식 더미 데이터(오프라인 개발·테스트용)"
            ),
        },
    }
