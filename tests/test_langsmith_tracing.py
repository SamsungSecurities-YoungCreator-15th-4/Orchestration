"""LangSmith 연동·감사정보 단위 테스트 — 네트워크와 실제 API key 불필요."""

import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm.audit import prompt_hash_record, with_llm_audit
from app.graph import build_graph
from app.nodes.assemble_report import assemble_report
from app.nodes.judge_eval import _AuditedLLM, _named_prompts, judge_eval
from app.nodes.load_inputs import load_inputs
from app.observability.langsmith import (
    annotate_current_run,
    langsmith_enabled,
    merge_observability,
    prepare_trace_invocation,
)


class _PassingLLM:
    model_name = "gpt-4o-test"
    deployment_name = "test-deployment"

    def invoke(self, prompt: str):
        return json.dumps({"passed": True, "reason": "통과"}, ensure_ascii=False)


def _judge_state() -> dict:
    return {
        "run_config": {"as_of_date": "2026-07-03"},
        "trace_id": "run-test",
        "demo_options": {"force_judge_fail": 1},
        "approval": {"status": "locked"},
        "metrics": {
            "confidence": 0.99,
            "horizons": {"1d": {"var_krw": 30_000_000}},
            "meta": {
                "computation_hash": "metric-hash",
                "data_period": {"end": "2026-07-03"},
            },
        },
        "explanations": [
            {
                "topic": "VaR 해석",
                "text": (
                    "기준일은 2026-07-03입니다. 99% 1일 VaR는 30,000,000원이며 "
                    "투자 권유가 아니고 원금 또는 수익을 보장하지 않습니다."
                ),
            }
        ],
        "citations": [
            {
                "claim": "VaR 해석",
                "quote": "99% 1일 VaR 설명",
                "source": "methodology_var_cvar_2026.pdf",
                "chunk_id": "methodology_var_cvar_2026.pdf::0001",
                "verified": True,
                "extra": {"chunk_text": "99% 1일 VaR 설명", "category": "methodology"},
            }
        ],
    }


def test_missing_api_key_disables_langsmith(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://apac.api.smith.langchain.com")
    monkeypatch.setenv("LANGSMITH_PROJECT", "Orchestration_Team4")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    invocation = prepare_trace_invocation(
        {"configurable": {"thread_id": "test"}},
        phase="analysis",
    )

    assert langsmith_enabled() is False
    assert invocation.enabled is False
    assert invocation.observability["langsmith_trace_url"] is None
    assert "run_id" not in invocation.config


def test_enabled_invocation_connects_trace_id_and_root_run(monkeypatch):
    fixed_run_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://apac.api.smith.langchain.com")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-only-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "Orchestration_Team4")
    monkeypatch.delenv("LANGSMITH_HIDE_INPUTS", raising=False)
    monkeypatch.delenv("LANGSMITH_HIDE_OUTPUTS", raising=False)
    monkeypatch.setattr("app.observability.langsmith.uuid.uuid4", lambda: fixed_run_id)
    monkeypatch.setattr(
        "app.observability.langsmith._get_run_url",
        lambda *_args, **_kwargs: "https://smith.example/trace/1234",
    )

    invocation = prepare_trace_invocation(
        {"configurable": {"thread_id": "test"}},
        phase="analysis",
        trace_id="run-domain-trace",
    )

    assert invocation.enabled is True
    assert invocation.config["run_id"] == fixed_run_id
    assert invocation.config["metadata"]["trace_id"] == "run-domain-trace"
    assert invocation.observability["langsmith_run_id"] == str(fixed_run_id)
    assert invocation.observability["langsmith_trace_url"].endswith("/1234")
    assert invocation.observability["hide_inputs"] is True
    assert invocation.observability["hide_outputs"] is True
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "true"
    assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "true"
    assert invocation.observability["phases"]["analysis"]["langsmith_run_id"] == str(
        fixed_run_id
    )


def test_enabled_invocation_ignores_malformed_metadata_and_tags(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://apac.api.smith.langchain.com")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-only-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "Orchestration_Team4")
    monkeypatch.setattr(
        "app.observability.langsmith._get_run_url",
        lambda *_args, **_kwargs: "https://smith.example/trace/1234",
    )

    invocation = prepare_trace_invocation(
        {"metadata": "invalid", "tags": ["kept", 1, None]},
        phase="analysis",
        trace_id="run-domain-trace",
    )

    assert invocation.config["metadata"] == {
        "trace_id": "run-domain-trace",
        "graph_phase": "analysis",
    }
    assert invocation.config["tags"] == ["kept", "risk-report", "phase:analysis"]


def test_run_annotation_failure_does_not_break_execution(monkeypatch, caplog):
    class BrokenRun:
        def add_metadata(self, metadata):
            raise RuntimeError("simulated SDK failure")

        def add_tags(self, tags):
            raise AssertionError("metadata failure should stop annotation")

    monkeypatch.setattr(
        "langsmith.run_helpers.get_current_run_tree",
        lambda: BrokenRun(),
    )

    annotate_current_run(metadata={"judge_retries": 1}, tags=["judge:failed"])

    assert "LangSmith run 어노테이션 실패" in caplog.text


def test_env_example_is_opt_in_and_masks_financial_payloads():
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env.example")

    assert values["LANGSMITH_TRACING"] == "false"
    assert values["LANGSMITH_HIDE_INPUTS"] == "true"
    assert values["LANGSMITH_HIDE_OUTPUTS"] == "true"


def test_observability_merge_preserves_input_and_analysis_traces():
    input_observability = {
        "phase": "input",
        "langsmith_run_id": "input-run",
        "langsmith_trace_url": "https://smith.example/input",
    }
    analysis_observability = {
        "phase": "analysis",
        "langsmith_run_id": "analysis-run",
        "langsmith_trace_url": "https://smith.example/analysis",
    }

    merged = merge_observability(input_observability, analysis_observability)

    assert merged["langsmith_trace_url"].endswith("/analysis")
    assert merged["phases"] == {
        "analysis": {
            "langsmith_run_id": "analysis-run",
            "langsmith_trace_url": "https://smith.example/analysis",
        },
        "input": {
            "langsmith_run_id": "input-run",
            "langsmith_trace_url": "https://smith.example/input",
        },
    }


def test_observability_does_not_change_config_hash():
    baseline = load_inputs({})
    traced = load_inputs(
        {
            "trace_id": "run-observed",
            "run_config": {
                "observability": {
                    "langsmith_enabled": True,
                    "langsmith_trace_url": "https://smith.example/trace/1",
                }
            },
        }
    )

    assert traced["run_config"]["config_hash"] == baseline["run_config"]["config_hash"]
    assert traced["trace_id"] == "run-observed"
    assert traced["run_config"]["observability"]["langsmith_enabled"] is True


def test_prompt_hash_is_deterministic_and_sensitive():
    first = prompt_hash_record({"VaR": "같은 프롬프트", "Stress": "근거"})
    second = prompt_hash_record({"Stress": "근거", "VaR": "같은 프롬프트"})
    changed = prompt_hash_record({"VaR": "다른 프롬프트", "Stress": "근거"})

    assert first == second
    assert first["aggregate_sha256"] != changed["aggregate_sha256"]


def test_llm_audit_replaces_malformed_nested_history():
    updated = with_llm_audit(
        {
            "audit": {
                "llm": {
                    "rag_cite": {
                        "history": "invalid",
                    }
                }
            }
        },
        component="rag_cite",
        attempt=1,
        prompts={"VaR": "prompt"},
    )

    history = updated["audit"]["llm"]["rag_cite"]["history"]
    assert [record["attempt"] for record in history] == [1]


def test_audited_llm_delegates_runnable_arguments_and_attributes():
    class ConfigAwareLLM:
        model_name = "delegated-model"

        def invoke(self, prompt, config=None, **kwargs):
            return {"prompt": prompt, "config": config, "kwargs": kwargs}

    wrapped = _AuditedLLM(ConfigAwareLLM())
    response = wrapped.invoke("prompt", {"tags": ["judge"]}, stop=["END"])

    assert response["config"] == {"tags": ["judge"]}
    assert response["kwargs"] == {"stop": ["END"]}
    assert wrapped.model_name == "delegated-model"
    assert wrapped.prompts == ["prompt"]
    assert wrapped.responses == [response]


def test_named_prompts_accepts_non_string_prompt_objects():
    class PromptValue:
        def __str__(self):
            return "판정 축: hallucination\n입력"

    assert _named_prompts([PromptValue()]) == {
        "hallucination": "판정 축: hallucination\n입력"
    }


def test_report_ignores_invalid_ips_extraction_metadata():
    state = _judge_state()
    state["ips_extraction_meta"] = "invalid"

    governance = assemble_report(state)["report"]["governance"]

    assert governance["model_versions"]["extract_ips"]["model"] is None
    assert governance["prompt_hashes"]["extract_ips"] is None


def test_judge_failure_adds_trace_metadata_and_report_audit(monkeypatch):
    captured = {}

    def capture_annotation(*, metadata, tags=None):
        captured["metadata"] = metadata
        captured["tags"] = tags

    monkeypatch.setattr("app.nodes.judge_eval.annotate_current_run", capture_annotation)
    state = _judge_state()
    state["run_config"]["observability"] = {
        "langsmith_project": "Orchestration_Team4",
        "langsmith_trace_url": "https://smith.example/trace/judge",
        "hide_inputs": True,
        "hide_outputs": True,
        "phases": {
            "input": {
                "langsmith_run_id": "input-run",
                "langsmith_trace_url": "https://smith.example/trace/input",
            },
            "analysis": {
                "langsmith_run_id": "analysis-run",
                "langsmith_trace_url": "https://smith.example/trace/judge",
            },
        },
    }

    judged = judge_eval(state, llm=_PassingLLM())
    report = assemble_report({**state, **judged})["report"]

    assert captured["metadata"]["failed_axes"] == ["forced_failure"]
    assert captured["metadata"]["judge_retries"] == 1
    assert "judge:failed" in captured["tags"]
    assert report["governance"]["trace_id"] == "run-test"
    assert report["governance"]["langsmith_trace_url"].endswith("/judge")
    assert report["governance"]["langsmith_trace_urls"] == {
        "analysis": "https://smith.example/trace/judge",
        "input": "https://smith.example/trace/input",
    }
    assert report["governance"]["langsmith_privacy"] == {
        "hide_inputs": True,
        "hide_outputs": True,
    }
    assert (
        report["governance"]["model_versions"]["judge_eval"]["model"] == "gpt-4o-test"
    )
    assert report["governance"]["prompt_hashes"]["judge_eval"]
    assert report["reproducibility"]["computation_hash"] == "metric-hash"


def test_hitl_resume_keeps_both_phase_trace_links(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    for name in (
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
    ):
        monkeypatch.setenv(name, "")

    graph = build_graph()
    config = {"configurable": {"thread_id": "trace-history-hitl"}}
    input_observability = {
        "phase": "input",
        "langsmith_run_id": "input-run",
        "langsmith_trace_url": "https://smith.example/input",
        "phases": {
            "input": {
                "langsmith_run_id": "input-run",
                "langsmith_trace_url": "https://smith.example/input",
            }
        },
    }
    list(
        graph.stream(
            {
                "trace_id": "run-hitl-test",
                "run_config": {"observability": input_observability},
                "demo_options": {"offline": True},
            },
            config,
            stream_mode="updates",
        )
    )
    snapshot = graph.get_state(config)
    assert "approval_gate" in snapshot.next

    analysis_observability = {
        "phase": "analysis",
        "langsmith_run_id": "analysis-run",
        "langsmith_trace_url": "https://smith.example/analysis",
    }
    run_config = dict(snapshot.values["run_config"])
    run_config["observability"] = merge_observability(
        run_config.get("observability"),
        analysis_observability,
    )
    graph.update_state(
        config,
        {
            "run_config": run_config,
            "approval": {
                "status": "reviewed",
                "decision": "approved",
                "approver": "test-pb",
                "note": "HITL trace 이력 테스트",
            },
        },
    )
    list(graph.stream(None, config, stream_mode="updates"))

    governance = graph.get_state(config).values["report"]["governance"]
    assert governance["langsmith_trace_urls"] == {
        "analysis": "https://smith.example/analysis",
        "input": "https://smith.example/input",
    }
