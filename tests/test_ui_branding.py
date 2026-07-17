"""Streamlit 브랜드 자산 로딩 방어 로직 테스트."""

from pathlib import Path

from ui.branding import logo_markup


def test_logo_markup_encodes_readable_asset(tmp_path: Path) -> None:
    dummy_logo = tmp_path / "symphony-logo.png"
    dummy_logo.write_bytes(b"fake-logo-bytes")

    markup = logo_markup(dummy_logo)

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
