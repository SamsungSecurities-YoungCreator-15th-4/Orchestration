"""release preflight 순수 로직 테스트."""
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from scripts.preflight_release import (
    EXPECTED_PDF_COUNTS,
    OFFLINE_ENV_KEYS,
    REQUIRED_GITIGNORE_PATTERNS,
    STREAMLIT_SECRET_KEYS,
    _parse_env_template,
    _parse_toml_template,
    _gitignore_patterns,
    command_check,
    corpus_pdf_counts,
    local_asset_checks,
    offline_environment,
    prerelease_requirement_pins,
    static_checks,
    streamlit_release_checks,
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


def test_parse_toml_template_handles_comments_quotes_and_booleans(tmp_path: Path):
    template = tmp_path / "secrets.toml.example"
    template.write_text(
        '# comment\nAZURE_OPENAI_API_KEY = ""\n'
        'LANGSMITH_ENDPOINT = "https://apac.api.smith.langchain.com" # region\n'
        "RAG_INDEX_REQUIRED = true\n",
        encoding="utf-8",
    )

    assert _parse_toml_template(template) == {
        "AZURE_OPENAI_API_KEY": "",
        "LANGSMITH_ENDPOINT": "https://apac.api.smith.langchain.com",
        "RAG_INDEX_REQUIRED": True,
    }


def test_gitignore_patterns_ignore_comments(tmp_path: Path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("# local only\n.env\ndata/chroma/\n", encoding="utf-8")

    assert _gitignore_patterns(gitignore) == {".env", "data/chroma/"}


def test_streamlit_release_contract_requires_safe_template_and_pinned_dependencies(
    tmp_path: Path,
):
    streamlit_dir = tmp_path / ".streamlit"
    streamlit_dir.mkdir()
    values = {
        key: '""'
        for key in STREAMLIT_SECRET_KEYS
    }
    values.update(
        {
            "AZURE_OPENAI_API_KEY": "''",
            "RAG_INDEX_REQUIRED": "true",
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_HIDE_INPUTS": "true",
            "LANGSMITH_HIDE_OUTPUTS": "true",
        }
    )
    (streamlit_dir / "secrets.toml.example").write_text(
        "\n".join(f"{key} = {value}" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "streamlit==1.52.1\nlanggraph==1.0.4\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text(
        ".streamlit/secrets.toml\n",
        encoding="utf-8",
    )

    assert all(result.status == "PASS" for result in streamlit_release_checks(tmp_path))


def test_prerelease_requirement_pins_detects_pep440_prereleases():
    requirements = [
        "langgraph==1.0.10rc1",
        "example-alpha==2.0.0a1",
        "example-beta==3.0.0b2",
        "example-dev==4.0.0.dev1",
        "example-preview==4.1.0-preview2",
        "candidate-local==4.2.0rc1+cpu",
        "stable-rc-name==5.0.0",
        "pyarrow==15.0.0",
        "scipy==1.15.3",
        "stable-cuda==6.0.0+cuda",
        "stable-abc==7.0.0+abc",
        "stable-post==8.0.0.post1",
        "unpinned-package>=1.0.0rc1",
    ]

    assert prerelease_requirement_pins(requirements) == [
        "candidate-local==4.2.0rc1+cpu",
        "example-alpha==2.0.0a1",
        "example-beta==3.0.0b2",
        "example-dev==4.0.0.dev1",
        "example-preview==4.1.0-preview2",
        "langgraph==1.0.10rc1",
    ]


def test_streamlit_release_contract_rejects_prerelease_pin(tmp_path: Path):
    streamlit_dir = tmp_path / ".streamlit"
    streamlit_dir.mkdir()
    values = {key: '""' for key in STREAMLIT_SECRET_KEYS}
    (streamlit_dir / "secrets.toml.example").write_text(
        "\n".join(f"{key} = {value}" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "langgraph==1.0.10rc1\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text(
        ".streamlit/secrets.toml\n",
        encoding="utf-8",
    )

    by_name = {
        result.name: result for result in streamlit_release_checks(tmp_path)
    }

    assert by_name["Stable dependency pins"].status == "FAIL"
    assert "langgraph==1.0.10rc1" in by_name["Stable dependency pins"].detail


def test_streamlit_release_contract_reports_missing_key_without_false_secret_alert(
    tmp_path: Path,
):
    streamlit_dir = tmp_path / ".streamlit"
    streamlit_dir.mkdir()
    values = {
        key: '""'
        for key in STREAMLIT_SECRET_KEYS - {"AZURE_OPENAI_API_KEY"}
    }
    (streamlit_dir / "secrets.toml.example").write_text(
        "\n".join(f"{key} = {value}" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "streamlit==1.52.1\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text(
        ".streamlit/secrets.toml\n",
        encoding="utf-8",
    )

    by_name = {
        result.name: result for result in streamlit_release_checks(tmp_path)
    }

    assert by_name["Streamlit secret key contract"].status == "FAIL"
    assert by_name["Streamlit secret placeholders"].status == "PASS"


def test_streamlit_release_contract_fails_on_secret_or_unpinned_dependency(
    tmp_path: Path,
):
    streamlit_dir = tmp_path / ".streamlit"
    streamlit_dir.mkdir()
    (streamlit_dir / "secrets.toml.example").write_text(
        'AZURE_OPENAI_API_KEY = "committed-secret"\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("streamlit\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")

    failed = {
        result.name for result in streamlit_release_checks(tmp_path)
        if result.status == "FAIL"
    }

    assert failed == {
        "Streamlit secret key contract",
        "Streamlit secret placeholders",
        "Streamlit dependency pins",
        "Streamlit local secrets gitignore",
    }


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


def test_static_checks_distinguish_git_grep_execution_failure(tmp_path: Path, monkeypatch):
    (tmp_path / ".env.example").write_text("AZURE_OPENAI_API_KEY=\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "\n".join(sorted(REQUIRED_GITIGNORE_PATTERNS)),
        encoding="utf-8",
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "strict_citation_gate: false\n",
        encoding="utf-8",
    )
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=128)

    monkeypatch.setattr("scripts.preflight_release.subprocess.run", fake_run)

    result = next(
        item for item in static_checks(tmp_path)
        if item.name == "tracked secret pattern scan"
    )

    assert result.status == "FAIL"
    assert result.detail == "git grep 실행 실패 (exit 128)"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


@pytest.mark.parametrize(
    ("metadata", "expected_detail"),
    [
        (None, "metadata가 dict가 아님"),
        ({"category": "unexpected", "source": "doc.pdf"}, "예상하지 못한 category"),
        ({"category": "macro"}, "source 누락"),
    ],
)
def test_local_asset_checks_fail_on_invalid_chroma_metadata(
    tmp_path: Path,
    monkeypatch,
    metadata,
    expected_detail: str,
):
    persist_dir = tmp_path / "data" / "chroma"
    persist_dir.mkdir(parents=True)
    (persist_dir / "chroma.sqlite3").write_bytes(b"index")

    class FakeCollection:
        def get(self, *, include):
            assert include == ["metadatas"]
            return {"metadatas": [metadata]}

    class FakeClient:
        def __init__(self, *, path: str):
            assert path == str(persist_dir)

        def get_collection(self, name: str):
            assert name
            return FakeCollection()

        def close(self):
            pass

    chromadb = ModuleType("chromadb")
    chromadb.PersistentClient = FakeClient
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)

    result = next(
        item for item in local_asset_checks(tmp_path)
        if item.name == "Chroma indexed sources"
    )

    assert result.status == "FAIL"
    assert expected_detail in result.detail
