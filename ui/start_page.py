"""S.ymphony 시작 화면 — 본 상담·리포트 화면 진입 전의 관문 페이지.

Claude Design "SSYC Sup-Sym 시작페이지" 프로젝트의 확정안 1c 스펙을 따른다.
이 모듈의 CSS는 시작 화면 렌더링 시에만 주입되고, "시작하기" 클릭 후
st.rerun()으로 전부 사라지므로 본 화면 스타일과 충돌하지 않는다.
"""
from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

# 실제 브랜드 마크(symphony-icon.png)에서 흰색 추출한 어두운 배경 전용 로고
_CLEF_B64 = base64.b64encode(
    (Path(__file__).parent / "assets" / "symphony-clef-white.png").read_bytes()
).decode()

_START_CSS = """
<style>
.stApp {
    background: radial-gradient(1300px 900px at 50% 40%,
        #16225F 0%, #0C1447 48%, #070C33 100%);
    font-family: Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI",
        "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
}
/* 로고 뒤 정적 글로우 (1c: 560px radial, 재현 리스크 없는 무애니메이션) */
.stApp::before {
    content: ""; position: fixed; width: 560px; height: 560px;
    left: 50%; top: 36%; transform: translate(-50%, -50%);
    background: radial-gradient(circle,
        rgba(74, 110, 235, .3) 0%, rgba(74, 110, 235, 0) 65%);
    pointer-events: none;
}
/* 시작 화면에서만 Streamlit 기본 헤더·툴바·footer를 숨긴다 */
[data-testid="stHeader"], [data-testid="stToolbar"], footer { display: none; }
[data-testid="stMainBlockContainer"] {
    min-height: 100vh; min-height: 100dvh;
    max-width: 100%;
    display: flex; flex-direction: column;
    justify-content: center; align-items: center;
    padding: 24px 16px;
}
/* Streamlit이 세로 블록을 컨테이너 높이만큼 늘리므로 내부에서도 수직 중앙 정렬.
   간격은 1c의 36/36/60px 리듬을 쓰기 위해 기본 gap을 없앤다. */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
    justify-content: center; gap: 0;
}
/* 버튼 element container는 내용 폭으로 줄어들므로 교차축 중앙으로 보낸다 */
.st-key-symphony_start { align-self: center; }
.start-hero { text-align: center; padding: 0 8px; position: relative; }
.start-hero img {
    width: clamp(120px, 22vw, 200px); height: auto; max-width: 100%;
    margin-bottom: 36px;
    filter: drop-shadow(0 0 28px rgba(140, 170, 255, .4));
}
.start-wordmark {
    font-size: clamp(2.6rem, 9vw, 5rem); font-weight: 800;
    color: #FFFFFF; letter-spacing: -.01em; line-height: 1.1;
}
.start-subtitle {
    margin: 36px 0 60px;
    font-size: clamp(1rem, 3vw, 1.25rem); font-weight: 400;
    color: #B9C4EE; line-height: 1.75;
}
.stButton { display: flex; justify-content: center; }
[data-testid="stBaseButton-primary"] {
    width: 240px; height: 58px; border: none; border-radius: 29px;
    background: linear-gradient(100deg, #0F5AE0 0%, #3D84FF 100%);
    color: #FFFFFF; font-size: 18px; font-weight: 600;
    font-family: Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI",
        "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
    box-shadow: 0 10px 32px rgba(27, 109, 255, .42);
}
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primary"]:active {
    background: linear-gradient(100deg, #1B6DFF 0%, #5C98FF 100%);
    color: #FFFFFF;
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
        f'<img src="data:image/png;base64,{_CLEF_B64}" alt="S.ymphony 로고">'
        '<div class="start-wordmark">S.ymphony</div>'
        '<p class="start-subtitle">재현가능·설명가능 리스크 리포트</p>'
        "</div>",
        unsafe_allow_html=True,
    )
    return st.button("시작하기", type="primary", key="symphony_start")
