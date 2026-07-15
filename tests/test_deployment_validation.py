"""실제 배포 그래프의 실패 폐쇄 검증 계약 테스트."""

from copy import deepcopy

import pytest

from app.deployment_validation import (
    EXPECTED_GRAPH_NODES,
    format_deployment_checks,
    validate_deployment_state,
)


def _passing_state() -> dict:
    categories = ("methodology", "macro", "house_view", "tax")
    return {
        "run_config": {
            "strict_citation_gate": True,
            "observability": {"langsmith_enabled": True},
        },
        "approval": {"status": "locked"},
        "citations": [
            {
                "source": f"{category}.pdf",
                "verified": True,
                "extra": {"category": category},
            }
            for category in categories
        ],
        "judge": {
            "passed": True,
            "score": 0.95,
            "checks": [
                {"name": "source_validity", "required": True, "passed": True},
                {"name": "freshness", "required": False, "passed": False},
            ],
        },
        "report": {
            "governance": {
                "strict_citation_gate": True,
                "langsmith_trace_urls": {
                    "input": "https://smith.example/input",
                    "analysis": "https://smith.example/analysis",
                },
                "langsmith_privacy": {
                    "hide_inputs": True,
                    "hide_outputs": True,
                },
            }
        },
    }


def _failed_names(state: dict, order: list[str] | None = None) -> set[str]:
    checks = validate_deployment_state(state, order or list(EXPECTED_GRAPH_NODES))
    return {check.name for check in checks if not check.passed}


def test_deployment_validation_passes_complete_contract():
    checks = validate_deployment_state(_passing_state(), list(EXPECTED_GRAPH_NODES))

    assert all(check.passed for check in checks)
    assert format_deployment_checks(checks).endswith("DEPLOYMENT_VALIDATION: PASS")


@pytest.mark.parametrize(
    ("mutate", "expected_failure"),
    [
        (
            lambda state: state["run_config"].update(strict_citation_gate=False),
            "strict citation gate",
        ),
        (
            lambda state: state["citations"].pop(),
            "four-category citation coverage",
        ),
        (
            lambda state: state["citations"][0].update(verified=False),
            "verified citations",
        ),
        (
            lambda state: state["judge"]["checks"][0].update(passed=False),
            "Judge required checks",
        ),
        (
            lambda state: state["judge"].update(checks=[]),
            "Judge required checks",
        ),
        (
            lambda state: state["report"]["governance"][
                "langsmith_trace_urls"
            ].pop("analysis"),
            "LangSmith tracing",
        ),
        (
            lambda state: state["report"]["governance"][
                "langsmith_privacy"
            ].update(hide_outputs=False),
            "LangSmith privacy masking",
        ),
    ],
)
def test_deployment_validation_fails_closed(mutate, expected_failure: str):
    state = deepcopy(_passing_state())
    mutate(state)

    assert expected_failure in _failed_names(state)


def test_deployment_validation_requires_all_graph_nodes_and_locked_approval():
    state = _passing_state()
    state["approval"]["status"] = "reviewed"
    order = [node for node in EXPECTED_GRAPH_NODES if node != "rag_cite"]

    assert _failed_names(state, order) == {"graph E2E nodes", "HITL approval lock"}
