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
    tracing = os.environ.get("LANGSMITH_TRACING", "").strip().lower() in _TRUTHY
    required = ("LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT", "LANGSMITH_PROJECT")
    return tracing and all(os.environ.get(name, "").strip() for name in required)


def _get_run_url(run_id: uuid.UUID, *, endpoint: str, api_key: str, project: str) -> str | None:
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
    project = os.environ.get("LANGSMITH_PROJECT", "").strip() or None
    config = dict(base_config)
    config.pop("run_id", None)
    config.pop("run_name", None)
    observability = {
        "langsmith_enabled": enabled,
        "langsmith_project": project,
        "langsmith_run_id": None,
        "langsmith_trace_url": None,
        "phase": phase,
    }
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

    metadata = dict(config.get("metadata") or {})
    metadata.update({"trace_id": correlation_id, "graph_phase": phase})
    tags = list(config.get("tags") or [])
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
    run.add_metadata(metadata)
    if tags:
        run.add_tags(tags)
