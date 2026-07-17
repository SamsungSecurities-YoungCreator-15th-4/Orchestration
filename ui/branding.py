"""Streamlit 브랜드 자산을 안전하게 렌더링하는 헬퍼."""

from __future__ import annotations

import base64
from pathlib import Path


def logo_markup(logo_path: Path) -> str:
    """로고를 data URI로 반환하고, 읽을 수 없으면 텍스트 로고로 대체한다."""

    try:
        logo_bytes = logo_path.read_bytes()
    except OSError:
        logo_bytes = b""

    if not logo_bytes:
        return '<span class="logo-fallback" aria-label="S.ymphony">S.ymphony</span>'

    encoded = base64.b64encode(logo_bytes).decode("ascii")
    return f'<img src="data:image/png;base64,{encoded}" alt="S.ymphony">'
