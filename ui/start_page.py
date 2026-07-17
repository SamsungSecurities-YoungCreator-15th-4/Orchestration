"""S.ymphony 시작 화면 — 본 상담·리포트 화면 진입 전의 관문 페이지.

이 모듈의 CSS는 시작 화면 렌더링 시에만 주입되고, "시작하기" 클릭 후
st.rerun()으로 전부 사라지므로 본 화면 스타일과 충돌하지 않는다.
"""
from __future__ import annotations

import streamlit as st

# ui/assets/symphony-icon.png(백조형 이중 S 마크)의 형태를 참고해 만든
# 어두운 배경 전용 SVG 마크. PNG 원본은 밝은 배경용 파란 톤이라 그대로 쓰지 않는다.
START_LOGO_SVG = (
    '<svg viewBox="0 0 140 120" role="img" aria-label="S.ymphony 로고" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<path d="M6 86 C34 106 60 108 78 93" stroke="#FFFFFF" stroke-width="10" '
    'stroke-linecap="round"/>'
    '<path d="M96 18 C74 12 58 24 62 38 C66 52 92 54 96 68 C100 82 84 96 62 90" '
    'stroke="#FFFFFF" stroke-width="10" stroke-linecap="round"/>'
    '<path d="M112 30 C94 22 78 32 82 46 C86 60 110 62 112 76 C114 90 98 102 78 98" '
    'stroke="#9DBAFF" stroke-width="8" stroke-linecap="round" opacity="0.9"/>'
    "</svg>"
)

_START_CSS = """
<style>
.stApp { background: #101C50; }
/* 시작 화면에서만 Streamlit 기본 헤더·툴바·footer를 숨긴다 */
[data-testid="stHeader"], [data-testid="stToolbar"], footer { display: none; }
[data-testid="stMainBlockContainer"] {
    min-height: 100vh; min-height: 100dvh;
    max-width: 100%;
    display: flex; flex-direction: column;
    justify-content: center; align-items: center;
    padding: 24px 16px;
}
/* Streamlit이 세로 블록을 컨테이너 높이만큼 늘리므로 내부에서도 수직 중앙 정렬 */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
    justify-content: center;
}
/* 버튼 element container는 내용 폭으로 줄어들므로 교차축 중앙으로 보낸다 */
.st-key-symphony_start { align-self: center; }
.start-hero { text-align: center; padding: 0 8px; }
.start-hero svg {
    width: clamp(76px, 16vw, 112px); height: auto;
    margin-bottom: 30px; max-width: 100%;
}
.start-wordmark {
    font-size: clamp(2.3rem, 8vw, 3.5rem); font-weight: 800;
    color: #FFFFFF; letter-spacing: -0.01em; line-height: 1.15;
    margin: 0 0 16px;
}
.start-subtitle {
    font-size: clamp(0.95rem, 3vw, 1.05rem); font-weight: 500;
    color: #C9D6F2; margin: 0 0 44px;
}
.stButton { display: flex; justify-content: center; }
[data-testid="stBaseButton-primary"] {
    background: #2E6BFF; color: #FFFFFF; border: none;
    border-radius: 999px; padding: 0.85rem 3.6rem;
    font-size: 1rem; font-weight: 700; letter-spacing: 0.02em;
    box-shadow: 0 10px 26px rgba(46, 107, 255, 0.38);
    transition: background 0.15s ease, box-shadow 0.15s ease,
        transform 0.15s ease;
}
[data-testid="stBaseButton-primary"]:hover {
    background: #1F5AEE; color: #FFFFFF;
    box-shadow: 0 12px 30px rgba(46, 107, 255, 0.5);
}
[data-testid="stBaseButton-primary"]:active {
    background: #1A4FD6; color: #FFFFFF; transform: translateY(1px);
}
[data-testid="stBaseButton-primary"]:focus-visible {
    outline: 3px solid #9DBAFF; outline-offset: 3px;
}
</style>
"""


def render_start_page() -> bool:
    """시작 화면을 그리고 '시작하기' 클릭 여부를 반환한다."""
    st.markdown(_START_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="start-hero">'
        f"{START_LOGO_SVG}"
        '<div class="start-wordmark">S.ymphony</div>'
        '<p class="start-subtitle">재현가능·설명가능 리스크 리포트</p>'
        "</div>",
        unsafe_allow_html=True,
    )
    return st.button("시작하기", type="primary", key="symphony_start")
