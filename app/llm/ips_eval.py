"""IPS 추출 회귀 데이터셋 로딩과 필드 단위 평가 유틸."""
from __future__ import annotations

import json
import math
from pathlib import Path

REQUIRED_CASE_KEYS = {"id", "input", "expected"}
ALLOWED_EXPECTED_FIELDS = {
    "Name",
    "Age",
    "Job",
    "Goal",
    "Asset",
    "Return",
    "Risk",
    "Time",
    "Liquidity",
    "liquidity_required_eok",
}


def load_eval_dataset(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as file:
        cases = json.load(file)
    if not isinstance(cases, list) or len(cases) < 20:
        raise ValueError("IPS 평가 데이터셋은 최소 20개 사례가 필요합니다.")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not REQUIRED_CASE_KEYS.issubset(case):
            raise ValueError("각 평가 사례에는 id, input, expected가 필요합니다.")
        if not isinstance(case["id"], str) or not case["id"].strip() or case["id"] in ids:
            raise ValueError(f"평가 사례 id가 비어 있거나 중복입니다: {case.get('id')}")
        ids.add(case["id"])
        if not isinstance(case["input"], str) or not case["input"].strip():
            raise ValueError(f"평가 사례 입력이 비어 있습니다: {case['id']}")
        expected = case["expected"]
        if not isinstance(expected, dict) or not expected:
            raise ValueError(f"expected가 비어 있습니다: {case['id']}")
        unknown = set(expected) - ALLOWED_EXPECTED_FIELDS
        if unknown:
            raise ValueError(f"지원하지 않는 expected 필드입니다: {sorted(unknown)}")
    return cases


def evaluate_case(case: dict, ips: dict, liquidity_required_krw: float | None) -> dict:
    """사례의 명시적 기대값만 평가해 표현 자유도가 큰 텍스트 필드 오탐을 줄인다."""
    actual = dict(ips)
    actual["liquidity_required_eok"] = (
        liquidity_required_krw / 100_000_000
        if liquidity_required_krw is not None
        else None
    )
    fields = []
    for name, expected in case["expected"].items():
        value = actual.get(name)
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            passed = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isclose(float(value), float(expected), rel_tol=0, abs_tol=1e-6)
            )
        else:
            passed = value == expected
        fields.append({"field": name, "expected": expected, "actual": value, "passed": passed})
    return {
        "id": case["id"],
        "passed": all(field["passed"] for field in fields),
        "fields": fields,
    }
