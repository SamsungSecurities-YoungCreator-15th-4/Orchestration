"""release preflight 순수 로직 테스트."""
import sys
from pathlib import Path

from scripts.preflight_release import (
    EXPECTED_PDF_COUNTS,
    OFFLINE_ENV_KEYS,
    _parse_env_template,
    _gitignore_patterns,
    command_check,
    corpus_pdf_counts,
    offline_environment,
)


def test_corpus_pdf_counts_by_category(tmp_path: Path):
    for category, expected in EXPECTED_PDF_COUNTS.items():
        directory = tmp_path / "corpus" / category
        directory.mkdir(parents=True)
        for index in range(expected):
            (directory / f"doc-{index}.pdf").write_bytes(b"pdf")

    assert corpus_pdf_counts(tmp_path) == EXPECTED_PDF_COUNTS


def test_parse_env_template_ignores_comments_and_preserves_non_secret_defaults(tmp_path: Path):
    template = tmp_path / ".env.example"
    template.write_text(
        "# comment\nAZURE_OPENAI_API_KEY=\n"
        "LANGSMITH_ENDPOINT=https://apac.api.smith.langchain.com\n",
        encoding="utf-8",
    )

    assert _parse_env_template(template) == {
        "AZURE_OPENAI_API_KEY": "",
        "LANGSMITH_ENDPOINT": "https://apac.api.smith.langchain.com",
    }


def test_gitignore_patterns_ignore_comments(tmp_path: Path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("# local only\n.env\ndata/chroma/\n", encoding="utf-8")

    assert _gitignore_patterns(gitignore) == {".env", "data/chroma/"}


def test_offline_environment_removes_external_credentials(monkeypatch):
    for key in OFFLINE_ENV_KEYS:
        monkeypatch.setenv(key, "test-secret")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    environment = offline_environment()

    assert all(key not in environment for key in OFFLINE_ENV_KEYS)
    assert environment["LANGSMITH_TRACING"] == "false"


def test_command_check_requires_semantic_success_text():
    result = command_check(
        "semantic",
        [sys.executable, "-c", "print('judge false')"],
        required_text="judge true",
    )

    assert result.status == "FAIL"
    assert "필수 출력 없음" in result.detail
