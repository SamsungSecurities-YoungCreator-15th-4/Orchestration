"""Streamlit 브랜드 자산 로딩 방어 로직 테스트."""

from pathlib import Path

from ui.branding import logo_markup


def test_logo_markup_uses_project_asset() -> None:
    markup = logo_markup(Path("ui/assets/symphony-logo.png"))

    assert markup.startswith('<img src="data:image/png;base64,')
    assert 'alt="S.ymphony"' in markup


def test_logo_markup_falls_back_when_asset_is_missing(tmp_path: Path) -> None:
    markup = logo_markup(tmp_path / "missing-logo.png")

    assert "<img" not in markup
    assert "S.ymphony" in markup


def test_logo_markup_falls_back_when_asset_is_empty(tmp_path: Path) -> None:
    empty_logo = tmp_path / "empty-logo.png"
    empty_logo.touch()

    markup = logo_markup(empty_logo)

    assert "<img" not in markup
    assert "S.ymphony" in markup
