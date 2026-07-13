"""LangSmith 연동·감사정보 단위 테스트 — 네트워크와 실제 API key 불필요."""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm.audit import prompt_hash_record
from app.nodes.assemble_report import assemble_report
from app.nodes.judge_eval import _AuditedLLM, _named_prompts, judge_eval
from app.nodes.load_inputs import load_inputs
from app.observability.langsmith import langsmith_enabled, prepare_trace_invocation


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
    }

    judged = judge_eval(state, llm=_PassingLLM())
    report = assemble_report({**state, **judged})["report"]

    assert captured["metadata"]["failed_axes"] == ["forced_failure"]
    assert captured["metadata"]["judge_retries"] == 1
    assert "judge:failed" in captured["tags"]
    assert report["governance"]["trace_id"] == "run-test"
    assert report["governance"]["langsmith_trace_url"].endswith("/judge")
    assert report["governance"]["model_versions"]["judge_eval"]["model"] == "gpt-4o-test"
    assert report["governance"]["prompt_hashes"]["judge_eval"]
    assert report["reproducibility"]["computation_hash"] == "metric-hash"
