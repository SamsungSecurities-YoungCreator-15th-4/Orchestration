"""config.yaml 로드 + 더미 포트폴리오/샘플 입력 생성 (결정론적)."""
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

SAMPLE_RAW_INPUT = (
    "고객: 50대 자영업자, 위탁자산 50억 원. 위험성향은 중립적이며, "
    "은퇴까지 약 10년의 투자기간을 상정한다. 최근 고금리·강달러 환경이 "
    "지속되는 상황에서 포트폴리오의 하방 리스크를 점검하고자 한다. "
    "사업 운영상 일부 유동성 확보가 필요할 수 있다."
)


def load_inputs(state: RiskState) -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    run_config = dict(config)
    run_config["config_hash"] = sha256_of_dict(config)

    return {
        "run_config": run_config,
        "trace_id": f"run-{run_config['config_hash'][:12]}",
        "raw_input": SAMPLE_RAW_INPUT,
        "portfolio": DUMMY_PORTFOLIO,
        "market_data_ref": {
            "source": "dummy",
            "as_of_date": config["as_of_date"],
            "note": "스켈레톤 단계 — 실제 시장데이터 미연결",
        },
    }
