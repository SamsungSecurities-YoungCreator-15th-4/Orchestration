"""Streamlit 고객 입력 화면의 기본 렌더링 테스트."""
import pytest
from streamlit.testing.v1 import AppTest

import ui.index_supply


@pytest.fixture(autouse=True)
def _prepared_rag_index(monkeypatch):
    """UI 구성 테스트는 인덱스 공급 성공 이후의 화면만 독립적으로 검증한다."""
    ui.index_supply._cached_ensure_index.clear()
    monkeypatch.setattr(
        ui.index_supply,
        "ensure_deployment_index",
        lambda **_kwargs: object(),
    )
    yield
    ui.index_supply._cached_ensure_index.clear()


def test_client_and_portfolio_inputs_render_without_exception():
    app = AppTest.from_file("ui/app.py").run(timeout=20)

    assert not app.exception
    assert len(app.text_area) == 1
    assert len(app.number_input) == 7  # 포트폴리오 6종 + judge 강제 실패 횟수(시연용)
    assert "IPS 추출 및 PB 검토 요청" in [button.label for button in app.button]


def test_report_renders_four_role_based_rag_sections():
    app = AppTest.from_file("ui/app.py")
    app.session_state["report"] = {
        "title": "테스트 리스크 리포트",
        "summary": {"portfolio": {}, "risk": {}},
        "evidence": {"verified_citation_count": 4, "citation_count": 4},
        "citations": [
            {
                "claim": category,
                "quote": f"{category} 근거",
                "source": f"{category}_202605.pdf",
                "verified": True,
                "extra": {"category": category, "published_at": "2026-05-01"},
            }
            for category in ("methodology", "macro", "house_view", "tax")
        ],
        "governance": {},
        "judge": {},
        "reproducibility": {},
    }

    app.run(timeout=20)

    assert not app.exception
    markdown = "\n".join(element.value for element in app.markdown)
    assert "정량 계산 방법론 [계산에 직접 사용됨]" in markdown
    assert "거시환경·스트레스 근거 [참고용 — 계산 근거 아님]" in markdown
    assert "자산시장 참고자료 [참고용 — 계산 근거 아님]" in markdown
    assert "세무 참고자료 [참고용 — 계산 근거 아님]" in markdown
    # 근거문장 칸의 긴 미분리 텍스트가 컬럼 폭을 왜곡하지 않도록 st.table 대신
    # 폭 고정(colgroup) 커스텀 HTML 표를 쓴다 — AppTest는 이를 markdown으로 노출한다.
    citation_table_html = [
        element.value for element in app.markdown
        if 'class="citation-table"' in element.value
    ]
    assert len(citation_table_html) == 4
    for category in ("methodology", "macro", "house_view", "tax"):
        assert any(f"{category}_202605.pdf" in html for html in citation_table_html)
