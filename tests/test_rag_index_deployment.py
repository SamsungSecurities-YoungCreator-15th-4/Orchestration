"""Azure Blob RAG 인덱스 공급·manifest·Streamlit 중단 테스트."""
from __future__ import annotations

import copy
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from app.rag.deployment import (
    INSTALLED_MANIFEST,
    PUBLIC_ERROR_MESSAGE,
    IndexSupplyError,
    IndexSupplySettings,
    _validate_https_url,
    create_index_artifact,
    ensure_deployment_index,
    inspect_chroma_index,
    load_index_supply_settings,
    load_source_contract,
    sha256_file,
    validate_manifest,
)
from app.rag.ingest import COLLECTION_NAME, infer_published_at
import ui.index_supply as index_supply
from ui.index_supply import prepare_index_or_stop


def _build_index_and_corpus(tmp_path: Path) -> tuple[Path, Path]:
    from chromadb import PersistentClient

    persist_dir = tmp_path / "source-index"
    corpus_dir = tmp_path / "corpus"
    contract = load_source_contract()
    client = PersistentClient(path=str(persist_dir))
    collection = client.create_collection(COLLECTION_NAME)
    ids: list[str] = []
    documents: list[str] = []
    embeddings: list[list[float]] = []
    metadatas: list[dict] = []
    index = 0
    for category, sources in contract.items():
        category_dir = corpus_dir / category
        category_dir.mkdir(parents=True)
        for source in sources:
            (category_dir / source).write_bytes(f"pdf:{source}".encode())
            document = f"근거 본문 {source}"
            ids.append(f"{source}::0000")
            documents.append(document)
            embeddings.append([float(index), 1.0])
            metadatas.append(
                {
                    "source": source,
                    "category": category,
                    "chunk_id": ids[-1],
                    "published_at": infer_published_at(source),
                    "char_start": 0,
                    "char_end": len(document),
                }
            )
            index += 1
    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    client.close()
    return persist_dir, corpus_dir


def test_inspect_chroma_index_closes_client_before_return(monkeypatch, tmp_path: Path):
    """검증 클라이언트를 닫아 Windows의 staging 디렉터리 rename 잠금을 해제한다."""
    import chromadb

    events: list[str] = []

    class FakeCollection:
        def get(self, *, include):
            assert include == ["metadatas"]
            events.append("get")
            return {"metadatas": [{"source": "doc.pdf", "category": "methodology"}]}

        def count(self):
            events.append("count")
            return 1

    class FakeClient:
        def __init__(self, *, path: str):
            assert path == str(tmp_path)

        def get_collection(self, name: str):
            assert name == COLLECTION_NAME
            events.append("get_collection")
            return FakeCollection()

        def close(self):
            events.append("close")

    monkeypatch.setattr(chromadb, "PersistentClient", FakeClient)

    summary = inspect_chroma_index(tmp_path)

    assert summary["chunk_count"] == 1
    assert events == ["get_collection", "get", "count", "close"]


def _package(tmp_path: Path) -> tuple[Path, Path, dict]:
    persist_dir, corpus_dir = _build_index_and_corpus(tmp_path)
    return create_index_artifact(
        index_version="2026-07-15.v1",
        persist_dir=persist_dir,
        corpus_dir=corpus_dir,
        output_dir=tmp_path / "artifacts",
        created_at="2026-07-15T12:00:00Z",
        pdf_text_loader=lambda path: f"근거 본문 {path.name}",
    )


def test_settings_prefer_streamlit_secrets_and_require_all_remote_values():
    settings = load_index_supply_settings(
        {
            "RAG_INDEX_BLOB_URL": "https://secret.example/index.zip?sas",
            "RAG_INDEX_MANIFEST_URL": "https://secret.example/index.json?sas",
            "RAG_INDEX_VERSION": "v1",
            "RAG_INDEX_SHA256": "a" * 64,
            "RAG_INDEX_REQUIRED": "true",
        },
        {
            "RAG_INDEX_BLOB_URL": "https://env.example/index.zip?sas",
        },
    )

    assert settings.artifact_url.startswith("https://secret.example/")
    assert settings.complete is True
    assert settings.required is True


def test_required_remote_config_does_not_silently_use_unmanaged_local_index(tmp_path: Path):
    local_index = tmp_path / "chroma"
    local_index.mkdir()
    (local_index / "chroma.sqlite3").write_bytes(b"local")

    with pytest.raises(IndexSupplyError, match="설정이 일부 누락"):
        ensure_deployment_index(
            settings=IndexSupplySettings(required=True),
            persist_dir=local_index,
            cache_dir=tmp_path / "cache",
        )


def test_unmanaged_local_index_is_still_checked_against_21_sources(tmp_path: Path):
    persist_dir, _corpus_dir = _build_index_and_corpus(tmp_path)

    result = ensure_deployment_index(
        settings=IndexSupplySettings(),
        persist_dir=persist_dir,
        cache_dir=tmp_path / "cache",
    )

    assert result.mode == "local"
    assert result.source_count == result.chunk_count == 21


def test_blob_url_without_sp_preserves_existing_https_validation():
    _validate_https_url(
        "https://account.blob.core.windows.net/private/index.zip?download=1"
    )

    with pytest.raises(IndexSupplyError, match="read-only SAS"):
        _validate_https_url("https://example.com/index.zip?sig=masked")
    with pytest.raises(IndexSupplyError, match="read-only SAS"):
        _validate_https_url("https://account.blob.core.windows.net/private/index.zip")


@pytest.mark.parametrize(
    ("parameter", "permission"),
    [("sp", "r"), ("sp", "R"), ("SP", "r")],
)
def test_blob_url_accepts_read_only_sas_permission_case_insensitively(
    parameter: str,
    permission: str,
):
    _validate_https_url(
        "https://account.blob.core.windows.net/private/index.zip"
        f"?{parameter}={permission}&sig=masked"
    )


@pytest.mark.parametrize(
    ("parameter", "permission"),
    [("sp", "rw"), ("sp", "rl"), ("sp", "RW"), ("SP", "rL")],
)
def test_blob_url_rejects_non_read_only_sas_permissions(
    parameter: str,
    permission: str,
):
    with pytest.raises(IndexSupplyError, match="읽기 전용 SAS만 허용"):
        _validate_https_url(
            "https://account.blob.core.windows.net/private/index.zip"
            f"?{parameter}={permission}&sig=masked"
        )


def test_create_artifact_records_21_pdf_checksums_and_chroma_counts(tmp_path: Path):
    archive_path, manifest_path, manifest = _package(tmp_path)

    assert archive_path.is_file()
    assert manifest_path.is_file()
    assert manifest["source_count"] == 21
    assert manifest["chunk_count"] == 21
    assert len(manifest["sources"]) == 21
    assert sum(row["source_count"] for row in manifest["categories"].values()) == 21
    assert manifest["artifact"]["sha256"] == sha256_file(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        assert all(not name.endswith(".pdf") for name in archive.namelist())


def test_create_artifact_rejects_stale_index_for_current_pdf(tmp_path: Path):
    persist_dir, corpus_dir = _build_index_and_corpus(tmp_path)

    with pytest.raises(IndexSupplyError, match="현재 corpus와 다릅니다"):
        create_index_artifact(
            index_version="2026-07-15.v1",
            persist_dir=persist_dir,
            corpus_dir=corpus_dir,
            output_dir=tmp_path / "artifacts",
            created_at="2026-07-15T12:00:00Z",
            pdf_text_loader=lambda path: (
                "변경된 최신 PDF 본문"
                if path.name == "bok_framework_2026.pdf"
                else f"근거 본문 {path.name}"
            ),
        )


def test_create_artifact_rejects_stale_published_at_metadata(tmp_path: Path):
    from chromadb import PersistentClient

    persist_dir, corpus_dir = _build_index_and_corpus(tmp_path)
    client = PersistentClient(path=str(persist_dir))
    try:
        collection = client.get_collection(COLLECTION_NAME)
        chunk_id = "bok_framework_2026.pdf::0000"
        stored = collection.get(ids=[chunk_id], include=["metadatas"])
        metadata = dict(stored["metadatas"][0])
        metadata["published_at"] = "2026-01-01"
        collection.update(ids=[chunk_id], metadatas=[metadata])
    finally:
        client.close()

    with pytest.raises(IndexSupplyError, match="현재 corpus와 다릅니다"):
        create_index_artifact(
            index_version="2026-07-15.v1",
            persist_dir=persist_dir,
            corpus_dir=corpus_dir,
            output_dir=tmp_path / "artifacts",
            created_at="2026-07-15T12:00:00Z",
            pdf_text_loader=lambda path: f"근거 본문 {path.name}",
        )


def test_manifest_rejects_source_count_and_secret_sha_mismatch(tmp_path: Path):
    _archive_path, _manifest_path, manifest = _package(tmp_path)
    contract = load_source_contract()
    invalid = copy.deepcopy(manifest)
    invalid["source_count"] = 20

    with pytest.raises(IndexSupplyError, match="source_count"):
        validate_manifest(
            invalid,
            expected_version=manifest["index_version"],
            expected_sha256=manifest["artifact"]["sha256"],
            source_contract=contract,
        )
    with pytest.raises(IndexSupplyError, match="SHA-256"):
        validate_manifest(
            manifest,
            expected_version=manifest["index_version"],
            expected_sha256="0" * 64,
            source_contract=contract,
        )


def test_blob_artifact_is_downloaded_validated_installed_and_cached(
    tmp_path: Path,
    monkeypatch,
):
    archive_path, manifest_path, manifest = _package(tmp_path)
    target = tmp_path / "deployed" / "chroma"
    calls = {"manifest": 0, "artifact": 0}

    def fake_download_bytes(url: str, *, limit: int) -> bytes:
        assert url.startswith("https://") and limit > 0
        calls["manifest"] += 1
        return manifest_path.read_bytes()

    def fake_download_file(url: str, destination: Path, *, limit: int) -> None:
        assert url.startswith("https://") and limit > 0
        calls["artifact"] += 1
        shutil.copyfile(archive_path, destination)

    monkeypatch.setattr("app.rag.deployment._download_bytes", fake_download_bytes)
    monkeypatch.setattr("app.rag.deployment._download_file", fake_download_file)
    settings = IndexSupplySettings(
        artifact_url="https://storage.example/index.zip?sas-secret",
        manifest_url="https://storage.example/index.json?sas-secret",
        expected_version=manifest["index_version"],
        expected_sha256=manifest["artifact"]["sha256"],
        required=True,
    )

    first = ensure_deployment_index(
        settings=settings,
        persist_dir=target,
        cache_dir=tmp_path / "cache",
    )
    second = ensure_deployment_index(
        settings=settings,
        persist_dir=target,
        cache_dir=tmp_path / "cache",
    )

    assert first.mode == "downloaded"
    assert second.mode == "cached"
    assert first.source_count == second.source_count == 21
    assert first.chunk_count == second.chunk_count == 21
    assert (target / INSTALLED_MANIFEST).is_file()
    assert calls == {"manifest": 1, "artifact": 1}


@pytest.mark.parametrize(
    ("corruption", "error_pattern"),
    [
        ("size", "크기가 manifest와 다릅니다"),
        ("sha256", "SHA-256 검증에 실패"),
    ],
)
def test_corrupted_download_is_removed_from_cache_immediately(
    tmp_path: Path,
    monkeypatch,
    corruption: str,
    error_pattern: str,
):
    archive_path, manifest_path, manifest = _package(tmp_path)
    original = archive_path.read_bytes()
    corrupted = (
        original[:-1]
        if corruption == "size"
        else bytes([original[0] ^ 0xFF]) + original[1:]
    )

    monkeypatch.setattr(
        "app.rag.deployment._download_bytes",
        lambda _url, *, limit: manifest_path.read_bytes(),
    )
    monkeypatch.setattr(
        "app.rag.deployment._download_file",
        lambda _url, destination, *, limit: destination.write_bytes(corrupted),
    )
    settings = IndexSupplySettings(
        artifact_url="https://storage.example/index.zip?sas",
        manifest_url="https://storage.example/manifest.json?sas",
        expected_version=manifest["index_version"],
        expected_sha256=manifest["artifact"]["sha256"],
        required=True,
    )
    cache_root = tmp_path / "cache"
    cached_artifact = (
        cache_root / manifest["index_version"] / manifest["artifact"]["filename"]
    )

    with pytest.raises(IndexSupplyError, match=error_pattern):
        ensure_deployment_index(
            settings=settings,
            persist_dir=tmp_path / "target",
            cache_dir=cache_root,
        )

    assert not cached_artifact.exists()


def test_unsafe_zip_path_is_rejected_before_install(tmp_path: Path, monkeypatch):
    _archive_path, manifest_path, manifest = _package(tmp_path)
    malicious = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../escape.txt", "blocked")
    manifest["artifact"] = {
        "filename": malicious.name,
        "sha256": sha256_file(malicious),
        "size_bytes": malicious.stat().st_size,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        "app.rag.deployment._download_bytes",
        lambda _url, *, limit: manifest_path.read_bytes(),
    )
    monkeypatch.setattr(
        "app.rag.deployment._download_file",
        lambda _url, destination, *, limit: shutil.copyfile(malicious, destination),
    )
    settings = IndexSupplySettings(
        artifact_url="https://storage.example/malicious.zip?sas",
        manifest_url="https://storage.example/manifest.json?sas",
        expected_version=manifest["index_version"],
        expected_sha256=manifest["artifact"]["sha256"],
        required=True,
    )

    with pytest.raises(IndexSupplyError, match="안전하지 않은 경로"):
        ensure_deployment_index(
            settings=settings,
            persist_dir=tmp_path / "target",
            cache_dir=tmp_path / "cache",
        )
    assert not (tmp_path / "escape.txt").exists()


def test_streamlit_shows_public_error_and_stops_without_secret_leak():
    class StopExecution(Exception):
        pass

    class Spinner:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeStreamlit:
        secrets = {}

        def __init__(self):
            self.errors: list[str] = []

        def spinner(self, _text: str):
            return Spinner()

        def error(self, message: str):
            self.errors.append(message)

        def stop(self):
            raise StopExecution

    st = FakeStreamlit()

    def fail(**_kwargs):
        raise IndexSupplyError("https://storage.example/index.zip?sas-secret")

    with pytest.raises(StopExecution):
        prepare_index_or_stop(st, ensure_index=fail)

    assert st.errors == [PUBLIC_ERROR_MESSAGE]
    assert "sas-secret" not in st.errors[0]


def test_streamlit_index_verification_is_cached_by_version_and_sha(monkeypatch):
    class Spinner:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeStreamlit:
        secrets = {
            "RAG_INDEX_BLOB_URL": (
                "https://account.blob.core.windows.net/private/index.zip?sig=masked"
            ),
            "RAG_INDEX_MANIFEST_URL": (
                "https://account.blob.core.windows.net/private/index.json?sig=masked"
            ),
            "RAG_INDEX_VERSION": "cache-review-v1",
            "RAG_INDEX_SHA256": "c" * 64,
            "RAG_INDEX_REQUIRED": "true",
        }

        def spinner(self, _text: str):
            return Spinner()

    calls = 0

    def succeed(**_kwargs):
        nonlocal calls
        calls += 1
        return object()

    index_supply._cached_ensure_index.clear()
    monkeypatch.setattr(index_supply, "ensure_deployment_index", succeed)
    st = FakeStreamlit()

    prepare_index_or_stop(st)
    prepare_index_or_stop(st)

    assert calls == 1
    index_supply._cached_ensure_index.clear()
