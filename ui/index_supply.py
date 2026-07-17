"""Streamlit 시작 시 배포 RAG 인덱스를 준비하고 실패를 명시적으로 중단한다."""
from __future__ import annotations

import logging

import streamlit as st

from app.rag.deployment import (
    PUBLIC_ERROR_MESSAGE,
    ensure_deployment_index,
    load_index_supply_settings,
)

log = logging.getLogger(__name__)


@st.cache_resource(show_spinner=False)
def _cached_ensure_index(
    index_version: str,
    expected_sha256: str,
    *,
    _settings,
    _ensure_index,
):
    """버전·SHA가 같은 설치본은 프로세스 수명 동안 최초 1회만 검증한다."""
    return _ensure_index(settings=_settings)


def prepare_index_or_stop(st_module, *, ensure_index=None):
    index_preparer = ensure_index or ensure_deployment_index
    try:
        try:
            secrets = dict(st_module.secrets)
        except Exception:
            secrets = {}
        settings = load_index_supply_settings(secrets)
        with st_module.spinner("분석 근거 및 참고 자료를 확인하고 있습니다..."):
            if ensure_index is not None:
                return index_preparer(settings=settings)
            return _cached_ensure_index(
                settings.expected_version,
                settings.expected_sha256,
                _settings=settings,
                _ensure_index=index_preparer,
            )
    except Exception as exc:
        # SAS URL·토큰이 UI나 로그에 노출되지 않도록 예외 문자열은 출력하지 않는다.
        log.error("RAG index bootstrap failed: %s", type(exc).__name__)
        st_module.error(PUBLIC_ERROR_MESSAGE)
        st_module.stop()
        return None
