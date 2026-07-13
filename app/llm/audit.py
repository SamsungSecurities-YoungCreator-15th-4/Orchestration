"""LLM 프롬프트·모델 정보를 비밀값 없이 결정론적으로 기록한다."""
from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence

from app.utils.hashing import sha256_of_dict


def prompt_hash_record(prompts: Mapping[str, str]) -> dict:
    """이름별 프롬프트 SHA256과 그 집합의 SHA256을 반환한다."""
    item_hashes = {
        str(name): hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
        for name, prompt in sorted(prompts.items(), key=lambda item: str(item[0]))
    }
    return {
        "aggregate_sha256": sha256_of_dict(item_hashes) if item_hashes else None,
        "items": item_hashes,
    }


def model_version_record(llm=None, responses: Sequence[object] = ()) -> dict:
    """Azure 배포명과 응답의 실제 모델명을 가능한 범위에서 수집한다."""
    deployment = None
    model = None
    if llm is not None:
        deployment = (
            getattr(llm, "deployment_name", None)
            or getattr(llm, "azure_deployment", None)
        )
        model = getattr(llm, "model_name", None) or getattr(llm, "model", None)

    for response in responses:
        metadata = getattr(response, "response_metadata", None)
        if isinstance(metadata, dict):
            model = metadata.get("model_name") or metadata.get("model") or model
            deployment = metadata.get("deployment_name") or deployment

    deployment = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    model = model or deployment
    return {
        "deployment": str(deployment) if deployment else None,
        "model": str(model) if model else None,
        "api_version": os.environ.get("AZURE_OPENAI_API_VERSION"),
    }


def with_llm_audit(
    run_config: dict,
    *,
    component: str,
    attempt: int,
    prompts: Mapping[str, str],
    llm=None,
    responses: Sequence[object] = (),
) -> dict:
    """기존 run_config를 보존하며 component별 최신/이력 감사를 기록한다."""
    updated = dict(run_config)
    audit = dict(updated.get("audit") or {})
    llm_audit = dict(audit.get("llm") or {})
    component_audit = dict(llm_audit.get(component) or {})
    record = {
        "attempt": attempt,
        "prompt_hash": prompt_hash_record(prompts),
        "model_version": model_version_record(llm, responses),
    }
    history = [
        item
        for item in component_audit.get("history") or []
        if isinstance(item, dict) and item.get("attempt") != attempt
    ]
    history.append(record)
    history.sort(key=lambda item: item["attempt"])
    llm_audit[component] = {"latest": record, "history": history}
    audit["llm"] = llm_audit
    updated["audit"] = audit
    return updated
