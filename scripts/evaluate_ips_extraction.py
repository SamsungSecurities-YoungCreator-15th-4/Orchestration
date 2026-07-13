"""Azure gpt-4o IPS 추출의 20사례 정확도·반복 일치율 평가.

사용 예:
  python scripts/evaluate_ips_extraction.py --repeats 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm.extract_ips_chain import extract_ips_profile_with_meta  # noqa: E402
from app.llm.ips_eval import evaluate_case, load_eval_dataset  # noqa: E402
from app.utils.hashing import sha256_of_dict  # noqa: E402

DEFAULT_DATASET = ROOT / "tests" / "fixtures" / "ips_extraction_cases.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="gpt-4o IPS 추출 회귀 평가")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--min-accuracy", type=float, default=0.95)
    parser.add_argument("--min-consistency", type=float, default=0.95)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verbose", action="store_true", help="사례별 상세 결과 출력")
    args = parser.parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats는 1 이상이어야 합니다.")

    cases = load_eval_dataset(args.dataset)
    results = []
    for case in cases:
        runs = []
        for _ in range(args.repeats):
            profile, liquidity_krw, metadata = extract_ips_profile_with_meta(case["input"])
            evaluation = evaluate_case(case, profile.model_dump(), liquidity_krw)
            evaluated_output_hash = sha256_of_dict(
                {
                    field["field"]: field["actual"]
                    for field in evaluation["fields"]
                }
            )
            runs.append(
                {
                    "evaluation": evaluation,
                    "output_hash": metadata["output_hash"],
                    "evaluated_output_hash": evaluated_output_hash,
                    "system_fingerprint": metadata.get("system_fingerprint"),
                }
            )
        results.append(
            {
                "id": case["id"],
                "runs": runs,
                "all_passed": all(run["evaluation"]["passed"] for run in runs),
                "repeat_consistent": len(
                    {run["evaluated_output_hash"] for run in runs}
                ) == 1,
                "full_output_consistent": len({run["output_hash"] for run in runs}) == 1,
            }
        )

    total_runs = len(cases) * args.repeats
    passed_runs = sum(
        run["evaluation"]["passed"] for result in results for run in result["runs"]
    )
    consistent_cases = sum(result["repeat_consistent"] for result in results)
    full_consistent_cases = sum(result["full_output_consistent"] for result in results)
    summary = {
        "dataset_size": len(cases),
        "repeats": args.repeats,
        "case_run_accuracy": passed_runs / total_runs,
        "repeat_consistency": consistent_cases / len(cases),
        "full_output_consistency": full_consistent_cases / len(cases),
        "thresholds": {
            "min_accuracy": args.min_accuracy,
            "min_consistency": args.min_consistency,
        },
        "results": results,
    }
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    console_summary = {key: value for key, value in summary.items() if key != "results"}
    print(json.dumps(summary if args.verbose else console_summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if (
        summary["case_run_accuracy"] < args.min_accuracy
        or summary["repeat_consistency"] < args.min_consistency
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
