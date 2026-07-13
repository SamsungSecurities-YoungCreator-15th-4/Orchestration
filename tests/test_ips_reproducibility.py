"""IPS 평가 데이터셋과 재현성 메타데이터 계약 테스트."""
from pathlib import Path

from app.llm.extract_ips_chain import extract_ips_profile_with_meta
from app.llm.ips_eval import evaluate_case, load_eval_dataset

DATASET = Path(__file__).parent / "fixtures" / "ips_extraction_cases.json"


class StableChain:
    def invoke(self, _values):
        return {
            "Name": "김민수",
            "Return": 5.0,
            "Time": 10.0,
            "Tax": "확인 필요",
            "Liquidity": "낮음",
            "Legal": "해당 사항 없음",
            "Unique": "사업 비상자금 필요",
            "liquidity_required_eok": 5.0,
        }


def test_eval_dataset_has_20_unique_valid_cases():
    cases = load_eval_dataset(DATASET)
    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20


def test_seeded_extraction_metadata_is_stable_for_same_input(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    first = extract_ips_profile_with_meta("고객 상담", chain=StableChain())
    second = extract_ips_profile_with_meta("고객 상담", chain=StableChain())

    assert first[0].Job == "자영업자"
    assert first[2]["seed"] == 42
    assert first[2]["prompt_version"] == "ips-extract-v2"
    assert first[2]["extraction_hash"] == second[2]["extraction_hash"]
    assert first[2]["output_hash"] == second[2]["output_hash"]


def test_case_evaluator_checks_explicit_fields_only():
    case = load_eval_dataset(DATASET)[0]
    result = evaluate_case(
        case,
        {
            "Name": "김민수",
            "Return": 5.0,
            "Time": 10.0,
            "Liquidity": "낮음",
            "Tax": "자유로운 표현",
        },
        500_000_000,
    )
    assert result["passed"] is True


def test_explicit_won_amounts_are_converted_deterministically():
    profile, liquidity_krw, _metadata = extract_ips_profile_with_meta(
        "남기훈 고객, 5년 투자, 목표 수익금 300,000,000원. "
        "사업자금으로 1,200,000,000원 필요.",
        chain=StableChain(),
    )
    assert profile.Return == 3.0
    assert profile.Liquidity == "중간"
    assert liquidity_krw == 1_200_000_000
