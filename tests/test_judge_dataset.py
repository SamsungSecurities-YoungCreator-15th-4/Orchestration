"""LangSmith Judge Dataset 등록 도구 테스트 — 네트워크·Azure 불필요."""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

from scripts.register_judge_dataset import (
    DATASET_NAME,
    _summary_accuracy,
    build_dataset_examples,
    dataset_summary,
    exact_match,
    predict_judge,
    upsert_dataset,
)


def test_dataset_examples_are_stable_15_plus_5_and_json_serializable():
    first = build_dataset_examples()
    second = build_dataset_examples()

    assert len(first) == 20
    assert [item["id"] for item in first] == [item["id"] for item in second]
    assert len({item["id"] for item in first}) == 20
    assert all(isinstance(item["id"], UUID) for item in first)
    assert sum(item["split"] == "deterministic" for item in first) == 15
    assert sum(item["split"] == "llm" for item in first) == 5
    json.dumps(first, default=str, ensure_ascii=False)


def test_dataset_examples_exclude_customer_input_contract_keys():
    examples = build_dataset_examples()

    for example in examples:
        state = example["inputs"]["state"]
        assert "raw_input" not in state
        assert "portfolio" not in state
        assert "ips" not in state
        assert "trace_id" not in state
        assert example["metadata"]["synthetic_only"] is True


def test_dataset_summary_does_not_expose_states():
    summary = dataset_summary(build_dataset_examples())

    assert summary == {
        "dataset_name": DATASET_NAME,
        "evalset_version": "judge-evalset-v1.1-20",
        "example_count": 20,
        "groups": {"deterministic": 15, "llm": 5},
        "case_ids": [f"EC-{index:02d}" for index in range(1, 21)],
        "contains_customer_input": False,
    }


class _Dataset:
    name = DATASET_NAME
    id = UUID("f0563183-9b6c-4587-82c9-775534390cc2")


class _FakeClient:
    def __init__(self, *, exists: bool, existing_ids: set[UUID] | None = None):
        self.exists = exists
        self.existing_ids = existing_ids or set()
        self.created = 0
        self.uploaded: list[dict] = []
        self.updated: list[UUID] = []

    def has_dataset(self, *, dataset_name: str) -> bool:
        assert dataset_name == DATASET_NAME
        return self.exists

    def read_dataset(self, *, dataset_name: str):
        assert dataset_name == DATASET_NAME
        return _Dataset()

    def create_dataset(self, dataset_name: str, **kwargs):
        assert dataset_name == DATASET_NAME
        assert kwargs["metadata"]["synthetic_only"] is True
        self.created += 1
        return _Dataset()

    def create_examples(self, *, dataset_id: UUID, examples: list[dict]):
        assert dataset_id == _Dataset.id
        self.uploaded = examples
        return {"count": len(examples)}

    def list_examples(self, *, dataset_id: UUID):
        assert dataset_id == _Dataset.id
        return [SimpleNamespace(id=example_id) for example_id in self.existing_ids]

    def update_example(self, example_id: UUID, **kwargs):
        assert kwargs["dataset_id"] == _Dataset.id
        assert kwargs["inputs"]["case_id"]
        self.updated.append(example_id)


def test_upsert_dataset_creates_missing_examples():
    client = _FakeClient(exists=False)
    examples = build_dataset_examples()

    dataset, response = upsert_dataset(client, DATASET_NAME, examples)

    assert dataset.id == _Dataset.id
    assert response == {"count": 20, "created": 20, "updated": 0}
    assert client.created == 1
    assert client.uploaded == examples
    assert client.updated == []


def test_upsert_dataset_updates_existing_examples_without_conflict():
    examples = build_dataset_examples()
    existing_ids = {example["id"] for example in examples}
    client = _FakeClient(exists=True, existing_ids=existing_ids)

    dataset, response = upsert_dataset(client, DATASET_NAME, examples)

    assert dataset.id == _Dataset.id
    assert response == {"count": 20, "created": 0, "updated": 20}
    assert client.created == 0
    assert client.uploaded == []
    assert set(client.updated) == existing_ids


def test_predict_deterministic_case_without_azure():
    example = next(
        item for item in build_dataset_examples()
        if item["inputs"]["case_id"] == "EC-04"
    )

    actual = predict_judge(example["inputs"])

    assert actual["passed"] is example["outputs"]["passed"]
    assert set(example["outputs"]["required_failed_axes"]) <= set(actual["failed_axes"])
    assert set(example["outputs"]["required_manual_review_flags"]) <= set(
        actual["manual_review_flags"]
    )


def test_exact_match_allows_additional_observed_failure_axes():
    run = SimpleNamespace(
        outputs={
            "passed": False,
            "failed_axes": ["source_validity", "verified_citations_present"],
            "manual_review_flags": [],
        }
    )
    example = SimpleNamespace(
        outputs={
            "passed": False,
            "required_failed_axes": ["source_validity"],
            "required_manual_review_flags": [],
        }
    )

    assert exact_match(run, example)["score"] == 1


def test_summary_accuracy_matches_runs_by_reference_example_id():
    first_id = UUID("f434eaec-daf7-4d9e-a73b-92a47527f7f2")
    second_id = UUID("73ed3eec-2d43-4c10-b723-651e7d9293af")
    first_example = SimpleNamespace(
        id=first_id,
        metadata={"evaluation_group": "deterministic"},
        outputs={
            "passed": True,
            "required_failed_axes": [],
            "required_manual_review_flags": [],
        },
    )
    second_example = SimpleNamespace(
        id=second_id,
        metadata={"evaluation_group": "deterministic"},
        outputs={
            "passed": False,
            "required_failed_axes": ["numeric_consistency"],
            "required_manual_review_flags": [],
        },
    )
    runs = [
        SimpleNamespace(
            reference_example_id=first_id,
            outputs={"passed": True, "failed_axes": [], "manual_review_flags": []},
        ),
        SimpleNamespace(
            reference_example_id=second_id,
            outputs={
                "passed": False,
                "failed_axes": ["numeric_consistency"],
                "manual_review_flags": [],
            },
        ),
    ]

    result = _summary_accuracy("deterministic")(runs, [second_example, first_example])

    assert result == {
        "key": "accuracy_deterministic",
        "score": 1.0,
        "comment": "2/2",
    }
