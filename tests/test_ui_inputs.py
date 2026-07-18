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


def test_start_page_renders_before_main_inputs():
    """새 세션은 시작 화면만 보이고, 본 입력 화면은 아직 렌더링되지 않는다."""
    app = AppTest.from_file("ui/app.py").run(timeout=20)

    assert not app.exception
    markdown = "\n".join(element.value for element in app.markdown)
    assert "S.ymphony" in markdown
    assert "재현가능·설명가능 리스크 리포트" in markdown
    assert "시작하기" in [button.label for button in app.button]
    assert "IPS 추출" not in [button.label for button in app.button]
    assert len(app.text_area) == 0


def test_start_button_moves_to_main_inputs():
    app = AppTest.from_file("ui/app.py").run(timeout=20)

    app.button(key="symphony_start").click().run(timeout=20)

    assert not app.exception
    assert app.session_state["symphony_started"] is True
    assert "IPS 추출" in [button.label for button in app.button]
    assert len(app.text_area) == 1


def test_client_and_portfolio_inputs_render_without_exception():
    app = AppTest.from_file("ui/app.py")
    app.session_state["symphony_started"] = True

    app.run(timeout=20)

    assert not app.exception
    assert len(app.text_area) == 1
    assert app.text_area[0].label == "고객 정보"
    assert app.text_area[0].value == ""
    assert len(app.number_input) == 7  # 포트폴리오 6종 + judge 강제 실패 횟수
    portfolio_defaults = {
        field.label: field.value
        for field in app.number_input
        if field.label.endswith("(%)")
    }
    assert portfolio_defaults == {
        "국내주식 (%)": 25.0,
        "해외주식 (%)": 20.0,
        "국내채권 (%)": 25.0,
        "해외채권 (%)": 15.0,
        "대체투자 (%)": 10.0,
        "현금성자산 (%)": 5.0,
    }
    styles = "\n".join(
        element.value for element in app.markdown if "<style>" in element.value
    )
    assert '[data-testid="stNumberInputContainer"]::after' in styles
    assert 'content: "%"' in styles
    assert "width: fit-content !important" in styles
    assert '[data-testid="stAppViewContainer"]' in styles
    assert "background: #EFF3FA !important" in styles
    assert '[data-testid="stBaseButton-primaryFormSubmit"]' in styles
    assert "background: #2563EB !important" in styles
    assert "[data-testid=\"stNumberInputContainer\"] input" in styles
    assert '[data-testid="stNumberInputContainer"] div' in styles
    assert "background-color: transparent !important" in styles
    assert "background-image: none !important" in styles
    assert "IPS 추출" in [button.label for button in app.button]


def test_pb_approval_hides_candidates_and_authorization_hint():
    app = AppTest.from_file("ui/app.py")
    app.session_state["pending_state"] = {
        "ips": {"Unique": "고금리·강달러 충격"},
        "portfolio": [],
        "conflicts": [],
    }

    app.run(timeout=20)

    assert not app.exception
    text_input_labels = [field.label for field in app.text_input]
    assert "PB 이름" in text_input_labels
    assert "PB 사번" in text_input_labels
    assert len(app.table) == 0
    assert not any("승인 권한 PB" in caption.value for caption in app.caption)


def test_report_renders_four_role_based_rag_sections():
    app = AppTest.from_file("ui/app.py")
    app.session_state["scroll_report_to_top"] = True
    app.session_state["report"] = {
        "title": "테스트 리스크 리포트",
        "as_of_date": "2026-07-03",
        "summary": {"portfolio": {"total_value_krw": 0}, "risk": {}},
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
        "governance": {"judge_passed": True},
        "judge": {},
        "reproducibility": {},
    }

    app.run(timeout=20)

    assert not app.exception
    assert "scroll_report_to_top" not in app.session_state
    markdown = "\n".join(element.value for element in app.markdown)
    assert 'id="report-page-top"' in markdown
    assert "리스크 정량 계산 근거" in markdown
    assert any(
        caption.value
        == "사내 공식 리스크 연산 문서를 바탕으로 정량 계산되었습니다."
        for caption in app.caption
    )
    assert "거시경제 근거" in markdown
    assert "House View 근거" in markdown
    assert "세금 이슈 근거" in markdown
    evidence_metrics = {metric.label: metric.value for metric in app.metric}
    assert evidence_metrics["유효한 검증 근거"] == "3건"
    assert evidence_metrics["전체 참고 자료"] == "3건"
    # 근거문장 칸의 긴 미분리 텍스트가 컬럼 폭을 왜곡하지 않도록 st.table 대신
    # 폭 고정(colgroup) 커스텀 HTML 표를 쓴다 — AppTest는 이를 markdown으로 노출한다.
    citation_table_html = [
        element.value for element in app.markdown
        if 'class="citation-table"' in element.value
    ]
    assert len(citation_table_html) == 4
    for category in ("methodology", "macro", "house_view", "tax"):
        assert any(f"{category}_202605.pdf" in html for html in citation_table_html)


def test_report_deduplicates_freshness_warnings_and_renders_stress_basis():
    app = AppTest.from_file("ui/app.py")
    house_citations = [
        {
            "claim": "자산시장 참고",
            "quote": f"House View 근거 {index}",
            "source": "samsung_equity_202511.pdf",
            "chunk_id": f"samsung_equity_202511.pdf::{index:04d}",
            "verified": True,
            "extra": {"category": "house_view", "published_at": "2025-11-01"},
        }
        for index in (1, 2)
    ]
    methodology_citation = {
        "claim": "스트레스 시나리오",
        "quote": "스트레스 테스트 방법론 근거",
        "source": "methodology_stress_2026.pdf",
        "chunk_id": "methodology_stress_2026.pdf::0001",
        "verified": True,
        "extra": {"category": "methodology", "published_at": "2026-07-01"},
    }
    var_methodology_citation = {
        "claim": "VaR·CVaR 방법론",
        "quote": "VaR·CVaR 정량 계산 방법론 근거",
        "source": "methodology_var_cvar_2026.pdf",
        "chunk_id": "methodology_var_cvar_2026.pdf::0001",
        "verified": True,
        "extra": {"category": "methodology", "published_at": "2026-07-01"},
    }
    freshness_detail = (
        "#1 house_view 8개월 경과 — 최신성 경고, "
        "#2 house_view 8개월 경과 — 최신성 경고"
    )
    app.session_state["report"] = {
        "title": "테스트 리스크 리포트",
        "as_of_date": "2026-07-03",
        "summary": {
            "portfolio": {"total_value_krw": 5_000_000_000},
            "risk": {
                "data_period": {
                    "start": "2021-09-07",
                    "end": "2026-07-03",
                    "n_observations": 1250,
                },
                "fx_rate_asof": 1542.13,
                "methodology_ref": "methodology_var_cvar_2026",
                "stress_scenario": "A_high_rate",
                "stress_scenario_count": 1,
                "stress_loss_krw": 890_000_000,
                "stress_loss_pct": 0.178,
                "stress_scenarios": [
                    {
                        "scenario": "A_high_rate",
                        "description": "고금리 충격",
                        "reference": "가상 스트레스 시나리오",
                        "loss_krw": 890_000_000,
                        "loss_krw_low": 697_750_000,
                        "loss_krw_high": 1_082_250_000,
                        "loss_pct": 0.178,
                        "loss_pct_low": 0.1395,
                        "loss_pct_high": 0.2165,
                    }
                ],
            },
        },
        "evidence": {"verified_citation_count": 3, "citation_count": 3},
        "citations": [
            *house_citations,
            var_methodology_citation,
            methodology_citation,
        ],
        "warnings": [freshness_detail],
        "governance": {"judge_passed": True},
        "judge": {
            "checks": [
                {
                    "name": "citation_publication_freshness",
                    "passed": False,
                    "required": False,
                    "detail": freshness_detail,
                }
            ]
        },
        "reproducibility": {},
    }

    app.run(timeout=20)

    assert not app.exception
    warning_html = next(
        element.value
        for element in app.markdown
        if 'class="warn-list"' in element.value
    )
    assert warning_html.count("samsung_equity_202511.pdf") == 1
    assert "#1 house_view" not in warning_html
    checks_html = next(
        element.value
        for element in app.markdown
        if 'class="checks-table"' in element.value
    )
    assert checks_html.count("samsung_equity_202511.pdf") == 1
    assert "#1 house_view" not in checks_html
    basis_tables = [
        element.value
        for element in app.markdown
        if 'class="basis-table"' in element.value
    ]
    assert len(basis_tables) == 2
    assert "2021-09-07 ~ 2026-07-03 (1250거래일)" in basis_tables[1]
    assert "1,542.13원" in basis_tables[1]
    assert "methodology_stress_2026.pdf" in basis_tables[1]
    citation_tables = [
        element.value
        for element in app.markdown
        if 'class="citation-table"' in element.value
    ]
    var_table = next(
        table for table in citation_tables if "methodology_var_cvar_2026.pdf" in table
    )
    stress_table = next(
        table for table in citation_tables if "methodology_stress_2026.pdf" in table
    )
    assert "methodology_stress_2026.pdf" not in var_table
    assert any(
        "스트레스 테스트 근거" in value
        for value in (element.value for element in app.markdown)
    )
    assert any(
        caption.value
        == "사내 공식 스트레스 연산 문서를 바탕으로 정량 계산되었습니다."
        for caption in app.caption
    )
    markdown_values = [element.value for element in app.markdown]
    assert markdown_values.index(basis_tables[1]) < markdown_values.index(stress_table)
    styles = "\n".join(
        element.value for element in app.markdown if "<style>" in element.value
    )
    assert "summary:hover" in styles
    assert "background: #FFFBEB !important" in styles
