"""Streamlit 시작 시 배포 RAG 인덱스를 준비하고 실패를 명시적으로 중단한다."""
from __future__ import annotations

import logging

from app.rag.deployment import (
    PUBLIC_ERROR_MESSAGE,
    ensure_deployment_index,
    load_index_supply_settings,
)

log = logging.getLogger(__name__)


def prepare_index_or_stop(st_module, *, ensure_index=None):
    index_preparer = ensure_index or ensure_deployment_index
    try:
        try:
            secrets = dict(st_module.secrets)
        except Exception:
            secrets = {}
        settings = load_index_supply_settings(secrets)
        with st_module.spinner("RAG 근거 인덱스를 확인하는 중..."):
            return index_preparer(settings=settings)
    except Exception as exc:
        # SAS URL·토큰이 UI나 로그에 노출되지 않도록 예외 문자열은 출력하지 않는다.
        log.error("RAG index bootstrap failed: %s", type(exc).__name__)
        st_module.error(PUBLIC_ERROR_MESSAGE)
        st_module.stop()
        return None
