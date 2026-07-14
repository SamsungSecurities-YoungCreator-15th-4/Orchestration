"""Judge 핵심 평가셋 20건을 LangSmith Dataset으로 등록·실험한다.

기본 실행은 네트워크를 사용하지 않는 dry-run이다. ``--upload`` 또는
``--run-experiment``를 명시한 경우에만 LangSmith API를 사용한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Sequence
from uuid import UUID, uuid5

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.nodes.judge_eval import judge_eval  # noqa: E402
from tests.test_judge_eval_evalset import (  # noqa: E402
    ALL_CASE_IDS,
    DETERMINISTIC_CASE_IDS,
    LLM_CASE_IDS,
    build_eval_case,
)

if TYPE_CHECKING:
    from langsmith import Client
    from langsmith.schemas import Example, Run

DATASET_NAME = "Orchestration_Judge_Evalset_v1"
EVALSET_VERSION = "judge-evalset-v1.1-20"
EXAMPLE_NAMESPACE = UUID("64591e8a-4896-4a39-8bc3-e84d446d0371")
FORBIDDEN_STATE_KEYS = frozenset({"raw_input", "portfolio", "ips", "trace_id"})


class _PassingLLM:
    """결정론 15건에서 LLM 축을 격리하는 평가용 fake."""

    def invoke(self, prompt: str) -> str:
        axis = "hallucination" if "판정 축: hallucination" in prompt else "false_precision"
        return json.dumps(
            {"passed": True, "reason": f"{axis} 격리용 통과"},
            ensure_ascii=False,
        )


def _group_for_case(case_id: str) -> str:
    if case_id in DETERMINISTIC_CASE_IDS:
        return "deterministic"
    if case_id in LLM_CASE_IDS:
        return "llm"
    raise ValueError(f"알 수 없는 평가셋 ID: {case_id}")


def build_dataset_examples(dataset_name: str = DATASET_NAME) -> list[dict]:
    """pytest 평가셋을 결정론적 ID를 가진 LangSmith 예제로 변환한다."""
    examples: list[dict] = []
    for case_id in ALL_CASE_IDS:
        spec = build_eval_case(case_id)
        state = deepcopy(spec["state"])
        forbidden = FORBIDDEN_STATE_KEYS.intersection(state)
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise ValueError(f"LangSmith 평가셋에 고객 입력 키를 포함할 수 없습니다: {names}")
        group = _group_for_case(case_id)
        examples.append(
            {
                "id": uuid5(EXAMPLE_NAMESPACE, f"{dataset_name}:{case_id}"),
                "inputs": {
                    "case_id": case_id,
                    "evaluation_group": group,
                    "state": state,
                },
                "outputs": {
                    "passed": spec["expected_passed"],
                    "required_failed_axes": sorted(spec["expected_axes"]),
                    "required_manual_review_flags": sorted(spec["expected_flags"]),
                },
                "metadata": {
                    "case_id": case_id,
                    "evaluation_group": group,
                    "evalset_version": EVALSET_VERSION,
                    "synthetic_only": True,
                },
                "split": group,
            }
        )
    return examples


def dataset_summary(examples: Sequence[dict]) -> dict:
    """비밀값이나 평가 입력을 출력하지 않는 등록 전 요약."""
    groups: dict[str, int] = {}
    case_ids: list[str] = []
    for example in examples:
        group = str(example["metadata"]["evaluation_group"])
        groups[group] = groups.get(group, 0) + 1
        case_ids.append(str(example["metadata"]["case_id"]))
    return {
        "dataset_name": DATASET_NAME,
        "evalset_version": EVALSET_VERSION,
        "example_count": len(examples),
        "groups": groups,
        "case_ids": sorted(case_ids),
        "contains_customer_input": False,
    }


def build_client() -> Client:
    """APAC LangSmith 평가 Client를 만든다. 값 자체는 출력하지 않는다.

    등록 전에 고객 입력 키를 거부한 합성 데이터만 다루므로, Dataset 실험에서
    기대값과 실제값을 비교할 수 있도록 이 Client에 한해 입출력을 표시한다.
    실제 그래프의 기본 마스킹 정책에는 영향을 주지 않는다.
    """
    from langsmith import Client

    required = ("LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError("LangSmith 연결 환경변수가 비어 있습니다: " + ", ".join(missing))
    return Client(
        api_url=os.environ["LANGSMITH_ENDPOINT"].strip(),
        api_key=os.environ["LANGSMITH_API_KEY"].strip(),
        hide_inputs=False,
        hide_outputs=False,
    )


def upsert_dataset(client: Client, dataset_name: str, examples: Sequence[dict]):
    """Dataset을 필요 시 생성하고 결정론적 example ID로 재실행 안전하게 갱신한다."""
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
    else:
        dataset = client.create_dataset(
            dataset_name,
            description="Judge 6축 핵심 평가셋: 결정론 15건 + Azure LLM 5건",
            metadata={
                "evalset_version": EVALSET_VERSION,
                "deterministic_count": len(DETERMINISTIC_CASE_IDS),
                "llm_count": len(LLM_CASE_IDS),
                "synthetic_only": True,
            },
        )
    existing_ids = {
        example.id for example in client.list_examples(dataset_id=dataset.id)
    }
    to_create: list[dict] = []
    updated = 0
    for example in examples:
        example_id = example["id"]
        if example_id not in existing_ids:
            to_create.append(example)
            continue
        client.update_example(
            example_id,
            inputs=example["inputs"],
            outputs=example["outputs"],
            metadata=example["metadata"],
            split=example["split"],
            dataset_id=dataset.id,
        )
        updated += 1

    if to_create:
        client.create_examples(dataset_id=dataset.id, examples=to_create)
    return dataset, {
        "count": len(examples),
        "created": len(to_create),
        "updated": updated,
    }


def _failed_axes(result: dict) -> list[str]:
    feedback = result.get("judge_feedback")
    if not feedback:
        return []
    return sorted(
        item["axis"]
        for item in json.loads(feedback).get("failed_axes", [])
    )


@lru_cache(maxsize=1)
def _azure_llm():
    from app.llm.client import get_llm

    return get_llm(temperature=0.0)


def predict_judge(inputs: dict) -> dict:
    """LangSmith experiment target. split에 따라 결정론/LLM 축을 분리 실행한다."""
    group = inputs.get("evaluation_group")
    if group not in {"deterministic", "llm"}:
        raise ValueError(f"알 수 없는 evaluation_group: {group}")
    llm = _PassingLLM() if group == "deterministic" else _azure_llm()
    result = judge_eval(deepcopy(inputs["state"]), llm=llm)
    judge = result["judge"]
    return {
        "passed": judge["passed"],
        "failed_axes": _failed_axes(result),
        "manual_review_flags": sorted(judge.get("manual_review_flags") or []),
    }


def exact_match(run: Run, example: Example) -> dict:
    """pytest와 동일하게 판정값 일치와 필수 축·플래그 포함 여부를 센다."""
    actual = run.outputs or {}
    expected = example.outputs or {}
    mismatched: list[str] = []
    if actual.get("passed") is not expected.get("passed"):
        mismatched.append("passed")
    if not set(expected.get("required_failed_axes") or []).issubset(
        actual.get("failed_axes") or []
    ):
        mismatched.append("required_failed_axes")
    if not set(expected.get("required_manual_review_flags") or []).issubset(
        actual.get("manual_review_flags") or []
    ):
        mismatched.append("required_manual_review_flags")
    return {
        "key": "judge_exact_match",
        "score": 0 if mismatched else 1,
        "comment": "일치" if not mismatched else "불일치 필드: " + ", ".join(mismatched),
    }


def _summary_accuracy(group: str):
    def evaluator(runs: Sequence[Run], examples: Sequence[Example]) -> dict:
        selected = [
            (run, example)
            for run, example in zip(runs, examples, strict=True)
            if (example.metadata or {}).get("evaluation_group") == group
        ]
        correct = sum(exact_match(run, example)["score"] for run, example in selected)
        score = correct / len(selected) if selected else 0.0
        return {
            "key": f"accuracy_{group}",
            "score": score,
            "comment": f"{correct}/{len(selected)}",
        }

    evaluator.__name__ = f"accuracy_{group}"
    return evaluator


def run_experiment(
    client: Client,
    *,
    dataset_name: str,
    group: str,
    max_concurrency: int,
):
    """선택 split을 실행하고 LangSmith에 행별·그룹별 정확도를 기록한다."""
    from langsmith.evaluation import evaluate

    splits = None if group == "all" else [group]
    examples = list(client.list_examples(dataset_name=dataset_name, splits=splits))
    if not examples:
        raise RuntimeError(f"실험할 LangSmith 예제가 없습니다: group={group}")
    return evaluate(
        predict_judge,
        data=examples,
        evaluators=[exact_match],
        summary_evaluators=[
            _summary_accuracy("deterministic"),
            _summary_accuracy("llm"),
        ],
        metadata={
            "evalset_version": EVALSET_VERSION,
            "evaluation_group": group,
            "azure_deployment": os.environ.get("AZURE_OPENAI_DEPLOYMENT", ""),
            "azure_api_version": os.environ.get("AZURE_OPENAI_API_VERSION", ""),
        },
        experiment_prefix="orchestration-judge",
        description="Judge 6축 평가셋 정확도: 결정론 15건 / LLM 5건 분리",
        max_concurrency=max_concurrency,
        client=client,
    )


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="LangSmith Judge 평가셋 등록·실험")
    parser.add_argument("--dataset-name", default=DATASET_NAME)
    parser.add_argument("--upload", action="store_true", help="Dataset과 20개 예제를 upsert")
    parser.add_argument("--run-experiment", action="store_true", help="등록된 Dataset으로 평가 실행")
    parser.add_argument(
        "--group",
        choices=("all", "deterministic", "llm"),
        default="all",
        help="실험 대상 split",
    )
    parser.add_argument("--max-concurrency", type=int, default=1)
    args = parser.parse_args()

    examples = build_dataset_examples(args.dataset_name)
    summary = dataset_summary(examples)
    summary["dataset_name"] = args.dataset_name
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.upload and not args.run_experiment:
        print("dry-run 완료: LangSmith API를 호출하지 않았습니다.")
        return

    client = build_client()
    if args.upload:
        dataset, response = upsert_dataset(client, args.dataset_name, examples)
        count = getattr(response, "count", None)
        if count is None and isinstance(response, dict):
            count = response.get("count")
        print(f"Dataset upsert 완료: name={dataset.name}, id={dataset.id}, examples={count}")
    elif not client.has_dataset(dataset_name=args.dataset_name):
        raise RuntimeError("Dataset이 없습니다. 먼저 --upload를 실행하세요.")

    if args.run_experiment:
        results = run_experiment(
            client,
            dataset_name=args.dataset_name,
            group=args.group,
            max_concurrency=args.max_concurrency,
        )
        print(f"LangSmith experiment 완료: {results.experiment_name}")


if __name__ == "__main__":
    main()
