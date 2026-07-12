"""Streamlit 고객 입력 화면의 기본 렌더링 테스트."""
from streamlit.testing.v1 import AppTest


def test_client_and_portfolio_inputs_render_without_exception():
    app = AppTest.from_file("ui/app.py").run(timeout=20)

    assert not app.exception
    assert len(app.text_area) == 1
    assert len(app.number_input) == 7  # 포트폴리오 6종 + judge 시연 옵션
    assert "IPS 추출 및 PB 검토 요청" in [button.label for button in app.button]
