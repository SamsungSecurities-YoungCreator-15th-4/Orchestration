"""배포용 Chroma 인덱스 아티팩트 생성·다운로드·무결성 검증.

원본 PDF와 Chroma는 Git에 넣지 않는다. 배포 환경은 private Azure Blob의
read-only SAS URL에서 ZIP과 sidecar manifest를 내려받고, 고정한 버전·SHA-256과
21개 source 계약을 모두 통과한 경우에만 ``data/chroma``로 원자적으로 교체한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Mapping
from urllib.parse import urlsplit

from app.rag.ingest import (
    CATEGORIES,
    COLLECTION_NAME,
    DEFAULT_CORPUS_DIR,
    DEFAULT_PERSIST_DIR,
    EMBEDDING_MODEL,
    chunk_text,
    contains_tbd,
    infer_published_at,
    load_pdf_text,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_CONTRACT = ROOT / "config" / "rag_sources.json"
INSTALLED_MANIFEST = ".rag-index-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
PUBLIC_ERROR_MESSAGE = (
    "RAG 근거 인덱스를 불러오지 못해 분석을 시작할 수 없습니다. "
    "잠시 후 다시 시도하거나 관리자에게 문의해 주세요."
)
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_AZURE_BLOB_HOST_SUFFIXES = (
    ".blob.core.windows.net",
    ".blob.core.usgovcloudapi.net",
    ".blob.core.chinacloudapi.cn",
)
_INDEX_INSTALL_LOCK = threading.Lock()


class IndexSupplyError(RuntimeError):
    """비밀값을 포함하지 않는 배포 인덱스 준비 오류."""


@dataclass(frozen=True)
class IndexSupplySettings:
    """Streamlit secrets 또는 환경 변수에서 읽는 배포 인덱스 고정값."""

    artifact_url: str = ""
    manifest_url: str = ""
    expected_version: str = ""
    expected_sha256: str = ""
    required: bool = False

    @property
    def configured(self) -> bool:
        return any(
            (
                self.artifact_url,
                self.manifest_url,
                self.expected_version,
                self.expected_sha256,
            )
        )

    @property
    def complete(self) -> bool:
        return all(
            (
                self.artifact_url,
                self.manifest_url,
                self.expected_version,
                self.expected_sha256,
            )
        )


@dataclass(frozen=True)
class IndexSupplyResult:
    mode: str
    index_version: str
    source_count: int
    chunk_count: int


def _text_value(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _bool_value(value: object) -> bool:
    return _text_value(value).lower() in {"1", "true", "yes", "on"}


def load_index_supply_settings(
    secrets: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> IndexSupplySettings:
    """secrets 우선, 환경 변수 차순으로 설정을 읽되 값 자체는 기록하지 않는다."""
    secret_values = {} if secrets is None else secrets
    environment = os.environ if environ is None else environ

    def value(key: str) -> str:
        secret = _text_value(secret_values.get(key))
        return secret or _text_value(environment.get(key))

    return IndexSupplySettings(
        artifact_url=value("RAG_INDEX_BLOB_URL"),
        manifest_url=value("RAG_INDEX_MANIFEST_URL"),
        expected_version=value("RAG_INDEX_VERSION"),
        expected_sha256=value("RAG_INDEX_SHA256").lower(),
        required=_bool_value(value("RAG_INDEX_REQUIRED")),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_source_contract(path: Path = DEFAULT_SOURCE_CONTRACT) -> dict[str, tuple[str, ...]]:
    """추적 가능한 21개 source 계약을 category별 불변 tuple로 읽는다."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        categories = payload["categories"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IndexSupplyError("source 계약 파일을 읽을 수 없습니다.") from exc
    if payload.get("schema_version") != 1 or not isinstance(categories, dict):
        raise IndexSupplyError("source 계약 schema가 올바르지 않습니다.")
    if set(categories) != set(CATEGORIES):
        raise IndexSupplyError("source 계약에는 4개 RAG category가 모두 필요합니다.")
    normalized: dict[str, tuple[str, ...]] = {}
    for category, sources in categories.items():
        if not isinstance(category, str) or not isinstance(sources, list):
            raise IndexSupplyError("source 계약 category 형식이 올바르지 않습니다.")
        clean = tuple(str(source).strip() for source in sources)
        if not clean or any(not source for source in clean) or len(clean) != len(set(clean)):
            raise IndexSupplyError("source 계약에 빈 값 또는 중복이 있습니다.")
        normalized[category] = clean
    source_count = sum(len(sources) for sources in normalized.values())
    if payload.get("source_count") != source_count or source_count != 21:
        raise IndexSupplyError("source 계약은 정확히 21건이어야 합니다.")
    return normalized


def _parse_created_at(value: object) -> None:
    if not isinstance(value, str):
        raise IndexSupplyError("manifest created_at이 누락되었습니다.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IndexSupplyError("manifest created_at 형식이 올바르지 않습니다.") from exc
    if parsed.tzinfo is None:
        raise IndexSupplyError("manifest created_at에는 timezone이 필요합니다.")


def validate_manifest(
    manifest: dict,
    *,
    expected_version: str,
    expected_sha256: str,
    source_contract: dict[str, tuple[str, ...]],
) -> None:
    """manifest 자체와 배포 secrets·21개 source 계약의 일치를 검증한다."""
    if not isinstance(manifest, dict):
        raise IndexSupplyError("manifest는 JSON object여야 합니다.")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise IndexSupplyError("지원하지 않는 manifest schema입니다.")
    index_version = manifest.get("index_version")
    if (
        not isinstance(index_version, str)
        or not _VERSION_RE.fullmatch(index_version)
        or index_version != expected_version
    ):
        raise IndexSupplyError("manifest index_version이 배포 설정과 다릅니다.")
    _parse_created_at(manifest.get("created_at"))
    if manifest.get("embedding_model") != EMBEDDING_MODEL:
        raise IndexSupplyError("manifest embedding_model이 현재 계약과 다릅니다.")
    if manifest.get("collection_name") != COLLECTION_NAME:
        raise IndexSupplyError("manifest collection_name이 현재 계약과 다릅니다.")
    if manifest.get("source_count") != 21:
        raise IndexSupplyError("manifest source_count는 정확히 21이어야 합니다.")
    chunk_count = manifest.get("chunk_count")
    if not isinstance(chunk_count, int) or isinstance(chunk_count, bool) or chunk_count <= 0:
        raise IndexSupplyError("manifest chunk_count가 올바르지 않습니다.")

    expected_sources = {
        source: category
        for category, sources in source_contract.items()
        for source in sources
    }
    source_rows = manifest.get("sources")
    if not isinstance(source_rows, list) or len(source_rows) != 21:
        raise IndexSupplyError("manifest sources는 정확히 21건이어야 합니다.")
    actual_sources: dict[str, str] = {}
    for row in source_rows:
        if not isinstance(row, dict):
            raise IndexSupplyError("manifest source 항목 형식이 올바르지 않습니다.")
        source = row.get("source")
        category = row.get("category")
        checksum = row.get("sha256")
        if (
            not isinstance(source, str)
            or not isinstance(category, str)
            or not isinstance(checksum, str)
            or not _SHA256_RE.fullmatch(checksum)
            or source in actual_sources
        ):
            raise IndexSupplyError("manifest source 항목이 유효하지 않습니다.")
        actual_sources[source] = category
    if actual_sources != expected_sources:
        raise IndexSupplyError("manifest source 목록 또는 category가 21개 계약과 다릅니다.")

    categories = manifest.get("categories")
    if not isinstance(categories, dict) or set(categories) != set(source_contract):
        raise IndexSupplyError("manifest category 목록이 현재 계약과 다릅니다.")
    category_chunk_total = 0
    for category, expected_category_sources in source_contract.items():
        summary = categories.get(category)
        if not isinstance(summary, dict):
            raise IndexSupplyError("manifest category 요약 형식이 올바르지 않습니다.")
        if summary.get("source_count") != len(expected_category_sources):
            raise IndexSupplyError("manifest category별 source 수가 계약과 다릅니다.")
        category_chunks = summary.get("chunk_count")
        if (
            not isinstance(category_chunks, int)
            or isinstance(category_chunks, bool)
            or category_chunks <= 0
        ):
            raise IndexSupplyError("manifest category별 chunk 수가 올바르지 않습니다.")
        category_chunk_total += category_chunks
    if category_chunk_total != chunk_count:
        raise IndexSupplyError("manifest category별 chunk 합계가 전체와 다릅니다.")

    artifact = manifest.get("artifact")
    if not isinstance(artifact, dict):
        raise IndexSupplyError("manifest artifact 항목이 누락되었습니다.")
    filename = artifact.get("filename")
    artifact_sha256 = artifact.get("sha256")
    size_bytes = artifact.get("size_bytes")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not filename.endswith(".zip")
    ):
        raise IndexSupplyError("manifest artifact filename이 올바르지 않습니다.")
    if (
        not isinstance(artifact_sha256, str)
        or not _SHA256_RE.fullmatch(artifact_sha256)
        or artifact_sha256 != expected_sha256
    ):
        raise IndexSupplyError("manifest artifact SHA-256이 배포 설정과 다릅니다.")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
        raise IndexSupplyError("manifest artifact 크기가 올바르지 않습니다.")


def inspect_chroma_index(persist_dir: Path) -> dict:
    """Chroma metadata에서 source·category·chunk 수를 읽어 검증 가능한 요약을 만든다."""
    try:
        from chromadb import PersistentClient

        collection = PersistentClient(path=str(persist_dir)).get_collection(COLLECTION_NAME)
        stored = collection.get(include=["metadatas"])
    except Exception as exc:
        raise IndexSupplyError("Chroma 컬렉션을 열 수 없습니다.") from exc
    metadatas = stored.get("metadatas") or []
    sources_by_category: dict[str, set[str]] = {}
    chunks_by_category: dict[str, int] = {}
    for index, metadata in enumerate(metadatas):
        if not isinstance(metadata, dict):
            raise IndexSupplyError(f"Chroma metadata 형식 오류: chunk {index}")
        source = metadata.get("source")
        category = metadata.get("category")
        if not isinstance(source, str) or not source.strip():
            raise IndexSupplyError(f"Chroma source 누락: chunk {index}")
        if not isinstance(category, str) or not category.strip():
            raise IndexSupplyError(f"Chroma category 누락: chunk {index}")
        sources_by_category.setdefault(category, set()).add(source)
        chunks_by_category[category] = chunks_by_category.get(category, 0) + 1
    return {
        "chunk_count": collection.count(),
        "sources_by_category": {
            category: tuple(sorted(sources))
            for category, sources in sources_by_category.items()
        },
        "chunks_by_category": chunks_by_category,
    }


def validate_chroma_index(
    persist_dir: Path,
    manifest: dict,
    source_contract: dict[str, tuple[str, ...]],
) -> dict:
    """압축 해제된 실제 Chroma가 manifest와 21개 source 계약에 일치하는지 검증한다."""
    summary = inspect_chroma_index(persist_dir)
    expected_by_category = {
        category: tuple(sorted(sources))
        for category, sources in source_contract.items()
    }
    if summary["sources_by_category"] != expected_by_category:
        raise IndexSupplyError("Chroma source 목록 또는 category가 manifest 계약과 다릅니다.")
    if summary["chunk_count"] != manifest.get("chunk_count"):
        raise IndexSupplyError("Chroma chunk 수가 manifest와 다릅니다.")
    manifest_chunks = {
        category: values["chunk_count"]
        for category, values in manifest["categories"].items()
    }
    if summary["chunks_by_category"] != manifest_chunks:
        raise IndexSupplyError("Chroma category별 chunk 수가 manifest와 다릅니다.")
    return summary


def verify_index_matches_corpus(
    persist_dir: Path,
    corpus_dir: Path,
    source_contract: dict[str, tuple[str, ...]],
    *,
    pdf_text_loader=None,
) -> None:
    """현재 21개 PDF의 결정론적 청크와 Chroma 본문·metadata가 정확히 같은지 확인한다."""
    loader = pdf_text_loader or load_pdf_text
    expected_chunks: dict[str, dict] = {}
    for category, sources in source_contract.items():
        category_dir = corpus_dir / category
        actual_pdf_names = (
            {path.name for path in category_dir.glob("*.pdf")}
            if category_dir.is_dir()
            else set()
        )
        if actual_pdf_names != set(sources):
            raise IndexSupplyError("현재 corpus PDF 목록이 21개 source 계약과 다릅니다.")
        for source in sources:
            pdf_path = category_dir / source
            try:
                text = loader(pdf_path)
            except Exception as exc:
                raise IndexSupplyError("corpus PDF 텍스트를 읽을 수 없습니다.") from exc
            if not isinstance(text, str) or not text.strip() or contains_tbd(text):
                raise IndexSupplyError("corpus PDF가 비어 있거나 미완성 상태입니다.")
            for chunk in chunk_text(
                text,
                source=source,
                category=category,
                published_at=infer_published_at(source),
            ):
                expected_chunks[chunk["chunk_id"]] = chunk

    try:
        from chromadb import PersistentClient

        collection = PersistentClient(path=str(persist_dir)).get_collection(COLLECTION_NAME)
        stored = collection.get(include=["documents", "metadatas"])
    except Exception as exc:
        raise IndexSupplyError("Chroma 원문 청크를 읽을 수 없습니다.") from exc
    ids = stored.get("ids") or []
    documents = stored.get("documents") or []
    metadatas = stored.get("metadatas") or []
    if not (len(ids) == len(documents) == len(metadatas) == len(expected_chunks)):
        raise IndexSupplyError("현재 corpus 청크 수와 Chroma chunk 수가 다릅니다.")
    for chunk_id, document, metadata in zip(ids, documents, metadatas):
        expected = expected_chunks.pop(chunk_id, None)
        if expected is None or not isinstance(metadata, dict):
            raise IndexSupplyError("Chroma chunk_id 또는 metadata가 현재 corpus와 다릅니다.")
        comparable_metadata = {
            key: metadata.get(key)
            for key in ("source", "category", "char_start", "char_end")
        }
        expected_metadata = {
            key: expected[key]
            for key in ("source", "category", "char_start", "char_end")
        }
        if document != expected["text"] or comparable_metadata != expected_metadata:
            raise IndexSupplyError("Chroma 본문 또는 metadata가 현재 corpus와 다릅니다.")
    if expected_chunks:
        raise IndexSupplyError("현재 corpus의 일부 chunk가 Chroma에 없습니다.")


def _validate_https_url(url: str) -> None:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname.endswith(_AZURE_BLOB_HOST_SUFFIXES)
        or not parsed.query
    ):
        raise IndexSupplyError("Azure Blob URL은 read-only SAS가 포함된 HTTPS 주소여야 합니다.")


def _download_bytes(url: str, *, limit: int) -> bytes:
    _validate_https_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "risk-index-bootstrap/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > limit:
                raise IndexSupplyError("다운로드 파일이 허용 크기를 초과합니다.")
            payload = response.read(limit + 1)
    except IndexSupplyError:
        raise
    except Exception as exc:
        raise IndexSupplyError("Azure Blob 다운로드에 실패했습니다.") from exc
    if len(payload) > limit:
        raise IndexSupplyError("다운로드 파일이 허용 크기를 초과합니다.")
    return payload


def _download_file(url: str, destination: Path, *, limit: int) -> None:
    _validate_https_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "risk-index-bootstrap/1"})
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > limit:
                raise IndexSupplyError("다운로드 파일이 허용 크기를 초과합니다.")
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise IndexSupplyError("다운로드 파일이 허용 크기를 초과합니다.")
                output.write(block)
    except IndexSupplyError:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise IndexSupplyError("Azure Blob 다운로드에 실패했습니다.") from exc


def _safe_extract_zip(artifact_path: Path, destination: Path) -> None:
    total_size = 0
    try:
        archive = zipfile.ZipFile(artifact_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise IndexSupplyError("RAG 인덱스 ZIP을 열 수 없습니다.") from exc
    with archive:
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if (
                not info.filename
                or "\\" in info.filename
                or member.is_absolute()
                or ".." in member.parts
                or mode == stat.S_IFLNK
            ):
                raise IndexSupplyError("RAG 인덱스 ZIP에 안전하지 않은 경로가 있습니다.")
            total_size += info.file_size
            if total_size > MAX_ARTIFACT_BYTES:
                raise IndexSupplyError("압축 해제 크기가 허용 범위를 초과합니다.")
            target = destination.joinpath(*member.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _read_installed_manifest(persist_dir: Path) -> dict | None:
    marker = persist_dir / INSTALLED_MANIFEST
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_install(staging: Path, persist_dir: Path) -> None:
    backup = persist_dir.parent / f".{persist_dir.name}.previous"
    if backup.exists():
        shutil.rmtree(backup)
    if persist_dir.exists():
        os.replace(persist_dir, backup)
    try:
        os.replace(staging, persist_dir)
    except Exception:
        if backup.exists() and not persist_dir.exists():
            os.replace(backup, persist_dir)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _ensure_deployment_index_unlocked(
    *,
    settings: IndexSupplySettings | None = None,
    persist_dir: str | Path = DEFAULT_PERSIST_DIR,
    cache_dir: str | Path = "data/rag-index-cache",
    source_contract_path: Path = DEFAULT_SOURCE_CONTRACT,
) -> IndexSupplyResult:
    """로컬 인덱스를 사용하거나 검증된 Blob 아티팩트를 설치한다.

    remote 설정이 없고 기존 로컬 인덱스가 있으면 개발 편의를 위해 그대로 사용한다.
    배포처럼 인덱스도 설정도 없는 경우와 remote 설정이 일부만 채워진 경우는 실패한다.
    """
    resolved = settings or load_index_supply_settings()
    target = Path(persist_dir)
    contract = load_source_contract(source_contract_path)

    if not resolved.complete:
        if resolved.configured or resolved.required:
            raise IndexSupplyError("RAG 인덱스 배포 설정이 일부 누락되었습니다.")
        if target.is_dir() and any(target.iterdir()):
            summary = inspect_chroma_index(target)
            expected_by_category = {
                category: tuple(sorted(sources))
                for category, sources in contract.items()
            }
            if summary["sources_by_category"] != expected_by_category:
                raise IndexSupplyError("로컬 Chroma source가 21개 계약과 다릅니다.")
            return IndexSupplyResult(
                "local",
                "local-unmanaged",
                21,
                summary["chunk_count"],
            )
        raise IndexSupplyError("RAG 인덱스와 Azure Blob 배포 설정이 모두 없습니다.")

    if not _VERSION_RE.fullmatch(resolved.expected_version):
        raise IndexSupplyError("RAG_INDEX_VERSION 형식이 올바르지 않습니다.")
    if not _SHA256_RE.fullmatch(resolved.expected_sha256):
        raise IndexSupplyError("RAG_INDEX_SHA256 형식이 올바르지 않습니다.")

    installed = _read_installed_manifest(target)
    if (
        installed
        and installed.get("index_version") == resolved.expected_version
        and (installed.get("artifact") or {}).get("sha256") == resolved.expected_sha256
    ):
        try:
            validate_manifest(
                installed,
                expected_version=resolved.expected_version,
                expected_sha256=resolved.expected_sha256,
                source_contract=contract,
            )
            summary = validate_chroma_index(target, installed, contract)
        except IndexSupplyError:
            pass
        else:
            return IndexSupplyResult(
                "cached",
                resolved.expected_version,
                21,
                summary["chunk_count"],
            )

    manifest_payload = _download_bytes(resolved.manifest_url, limit=MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(manifest_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndexSupplyError("RAG 인덱스 manifest JSON이 올바르지 않습니다.") from exc
    validate_manifest(
        manifest,
        expected_version=resolved.expected_version,
        expected_sha256=resolved.expected_sha256,
        source_contract=contract,
    )

    artifact = manifest["artifact"]
    cache_root = Path(cache_dir) / resolved.expected_version
    cache_root.mkdir(parents=True, exist_ok=True)
    artifact_path = cache_root / artifact["filename"]
    if (
        not artifact_path.is_file()
        or artifact_path.stat().st_size != artifact["size_bytes"]
        or sha256_file(artifact_path) != resolved.expected_sha256
    ):
        artifact_path.unlink(missing_ok=True)
        _download_file(
            resolved.artifact_url,
            artifact_path,
            limit=MAX_ARTIFACT_BYTES,
        )
    if artifact_path.stat().st_size != artifact["size_bytes"]:
        raise IndexSupplyError("RAG 인덱스 아티팩트 크기가 manifest와 다릅니다.")
    if sha256_file(artifact_path) != resolved.expected_sha256:
        raise IndexSupplyError("RAG 인덱스 아티팩트 SHA-256 검증에 실패했습니다.")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".rag-index-staging-", dir=target.parent))
    try:
        _safe_extract_zip(artifact_path, staging)
        summary = validate_chroma_index(staging, manifest, contract)
        (staging / INSTALLED_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _atomic_install(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return IndexSupplyResult(
        "downloaded",
        resolved.expected_version,
        21,
        summary["chunk_count"],
    )


def ensure_deployment_index(
    *,
    settings: IndexSupplySettings | None = None,
    persist_dir: str | Path = DEFAULT_PERSIST_DIR,
    cache_dir: str | Path = "data/rag-index-cache",
    source_contract_path: Path = DEFAULT_SOURCE_CONTRACT,
) -> IndexSupplyResult:
    """동일 Streamlit 프로세스의 동시 세션이 같은 설치 경로를 덮지 않게 직렬화한다."""
    with _INDEX_INSTALL_LOCK:
        return _ensure_deployment_index_unlocked(
            settings=settings,
            persist_dir=persist_dir,
            cache_dir=cache_dir,
            source_contract_path=source_contract_path,
        )


def create_index_artifact(
    *,
    index_version: str,
    persist_dir: str | Path = DEFAULT_PERSIST_DIR,
    corpus_dir: str | Path = DEFAULT_CORPUS_DIR,
    output_dir: str | Path = "data/rag-index-artifacts",
    source_contract_path: Path = DEFAULT_SOURCE_CONTRACT,
    created_at: str | None = None,
    pdf_text_loader=None,
) -> tuple[Path, Path, dict]:
    """검증된 Chroma ZIP과 PDF 체크섬을 포함한 sidecar manifest를 생성한다."""
    if not _VERSION_RE.fullmatch(index_version):
        raise IndexSupplyError("index_version 형식이 올바르지 않습니다.")
    index_path = Path(persist_dir)
    corpus_path = Path(corpus_dir)
    destination = Path(output_dir)
    contract = load_source_contract(source_contract_path)
    summary = inspect_chroma_index(index_path)
    expected_by_category = {
        category: tuple(sorted(sources))
        for category, sources in contract.items()
    }
    if summary["sources_by_category"] != expected_by_category:
        raise IndexSupplyError("패키징할 Chroma source가 21개 계약과 다릅니다.")
    verify_index_matches_corpus(
        index_path,
        corpus_path,
        contract,
        pdf_text_loader=pdf_text_loader,
    )

    source_rows: list[dict] = []
    for category, sources in contract.items():
        for source in sources:
            pdf_path = corpus_path / category / source
            if not pdf_path.is_file():
                raise IndexSupplyError("패키징에 필요한 corpus PDF가 누락되었습니다.")
            source_rows.append(
                {
                    "source": source,
                    "category": category,
                    "sha256": sha256_file(pdf_path),
                }
            )

    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / f"rag-index-{index_version}.zip"
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        files = sorted(path for path in index_path.rglob("*") if path.is_file())
        for path in files:
            if path.name == INSTALLED_MANIFEST:
                continue
            relative = path.relative_to(index_path).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, path.read_bytes())

    artifact_sha256 = sha256_file(archive_path)
    timestamp = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    categories = {
        category: {
            "source_count": len(sources),
            "chunk_count": summary["chunks_by_category"][category],
        }
        for category, sources in contract.items()
    }
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "index_version": index_version,
        "created_at": timestamp,
        "embedding_model": EMBEDDING_MODEL,
        "collection_name": COLLECTION_NAME,
        "source_count": 21,
        "chunk_count": summary["chunk_count"],
        "categories": categories,
        "sources": source_rows,
        "artifact": {
            "filename": archive_path.name,
            "sha256": artifact_sha256,
            "size_bytes": archive_path.stat().st_size,
        },
    }
    validate_manifest(
        manifest,
        expected_version=index_version,
        expected_sha256=artifact_sha256,
        source_contract=contract,
    )
    manifest_path = destination / f"rag-index-{index_version}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return archive_path, manifest_path, manifest
