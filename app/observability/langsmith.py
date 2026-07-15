"""LangGraph 실행과 LangSmith trace를 연결하는 얇은 표준부품 래퍼."""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Iterator

log = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


@dataclass(frozen=True)
class TraceInvocation:
    """그래프 한 번의 stream 호출에 필요한 trace 설정."""

    config: dict
    trace_id: str | None
    observability: dict
    enabled: bool
    project: str | None
    phase: str


def langsmith_enabled() -> bool:
    """명시적 tracing 설정과 API key가 모두 있을 때만 원격 추적을 켠다."""
    tracing = _env_flag("LANGSMITH_TRACING")
    required = ("LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT", "LANGSMITH_PROJECT")
    return tracing and all(os.environ.get(name, "").strip() for name in required)


def merge_observability(previous, current) -> dict:
    """HITL 전후 phase trace를 잃지 않고 최신 실행정보와 합친다."""
    previous = previous if isinstance(previous, dict) else {}
    current = current if isinstance(current, dict) else {}
    phases: dict[str, dict] = {}
    for source in (previous, current):
        raw_phases = source.get("phases")
        if isinstance(raw_phases, dict):
            phases.update(
                {
                    str(name): dict(value)
                    for name, value in raw_phases.items()
                    if isinstance(value, dict)
                }
            )
        phase = source.get("phase")
        if isinstance(phase, str) and phase:
            phases[phase] = {
                "langsmith_run_id": source.get("langsmith_run_id"),
                "langsmith_trace_url": source.get("langsmith_trace_url"),
            }
    phase_order = {"input": 0, "analysis": 1}
    ordered_phases = sorted(
        phases,
        key=lambda name: (phase_order.get(name, 2), name),
    )
    return {
        **previous,
        **current,
        "phases": {name: phases[name] for name in ordered_phases},
    }


def _get_run_url(
    run_id: uuid.UUID, *, endpoint: str, api_key: str, project: str
) -> str | None:
    """LangSmith 표준 Client로 아직 시작 전인 root run의 UI URL을 계산한다."""
    try:
        from langsmith import Client

        client = Client(api_url=endpoint, api_key=api_key)
        run_ref = SimpleNamespace(id=run_id, session_id=None, session_name=project)
        return client.get_run_url(run=run_ref)
    except Exception as exc:
        log.warning("LangSmith trace URL 생성 실패(트레이싱은 계속): %s", exc)
        return None


def prepare_trace_invocation(
    base_config: dict,
    *,
    phase: str,
    trace_id: str | None = None,
) -> TraceInvocation:
    """실행 config에 root run ID, tags, metadata를 추가한다.

    API key가 없으면 명시적으로 tracing을 끄고 기존 그래프 config를 그대로
    사용할 수 있게 한다. 비밀값은 반환 dict 어디에도 넣지 않는다.
    """
    enabled = langsmith_enabled()
    if enabled:
        # 기존 로컬 .env에 마스킹 키가 없어도 금융 상담 payload를 기본 전송하지 않는다.
        os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "true")
        os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "true")
    project = os.environ.get("LANGSMITH_PROJECT", "").strip() or None
    config = dict(base_config)
    config.pop("run_id", None)
    config.pop("run_name", None)
    observability = {
        "langsmith_enabled": enabled,
        "langsmith_project": project,
        "langsmith_run_id": None,
        "langsmith_trace_url": None,
        "hide_inputs": _env_flag("LANGSMITH_HIDE_INPUTS", default=True),
        "hide_outputs": _env_flag("LANGSMITH_HIDE_OUTPUTS", default=True),
        "phase": phase,
    }
    observability = merge_observability({}, observability)
    if not enabled:
        return TraceInvocation(
            config=config,
            trace_id=trace_id,
            observability=observability,
            enabled=False,
            project=project,
            phase=phase,
        )

    run_id = uuid.uuid4()
    correlation_id = trace_id or f"run-{run_id.hex[:12]}"
    endpoint = os.environ.get("LANGSMITH_ENDPOINT", "").strip()
    api_key = os.environ["LANGSMITH_API_KEY"].strip()
    trace_url = (
        _get_run_url(run_id, endpoint=endpoint, api_key=api_key, project=project)
        if endpoint and project
        else None
    )

    raw_metadata = config.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    metadata.update({"trace_id": correlation_id, "graph_phase": phase})
    raw_tags = config.get("tags")
    tags = (
        [tag for tag in raw_tags if isinstance(tag, str)]
        if isinstance(raw_tags, (list, tuple, set))
        else []
    )
    tags.extend(["risk-report", f"phase:{phase}"])
    config.update(
        {
            "run_id": run_id,
            "run_name": f"risk-report-{phase}",
            "metadata": metadata,
            "tags": list(dict.fromkeys(tags)),
        }
    )
    observability.update(
        {
            "langsmith_run_id": str(run_id),
            "langsmith_trace_url": trace_url,
        }
    )
    observability = merge_observability({}, observability)
    return TraceInvocation(
        config=config,
        trace_id=correlation_id,
        observability=observability,
        enabled=True,
        project=project,
        phase=phase,
    )


@contextmanager
def tracing_scope(invocation: TraceInvocation) -> Iterator[None]:
    """환경변수 오설정과 무관하게 이번 호출의 tracing on/off를 고정한다."""
    try:
        from langsmith.run_helpers import tracing_context
    except ImportError:
        yield
        return

    metadata = {
        "trace_id": invocation.trace_id,
        "graph_phase": invocation.phase,
    }
    with tracing_context(
        enabled=invocation.enabled,
        project_name=invocation.project,
        metadata=metadata,
        tags=["risk-report", f"phase:{invocation.phase}"],
    ):
        yield


def annotate_current_run(*, metadata: dict, tags: list[str] | None = None) -> None:
    """현재 LangSmith child run에 동적 판정정보를 추가한다."""
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
    except Exception:
        return
    if run is None:
        return
    try:
        run.add_metadata(metadata)
        if tags:
            run.add_tags(tags)
    except Exception as exc:
        log.warning("LangSmith run 어노테이션 실패(실행은 계속): %s", exc)
