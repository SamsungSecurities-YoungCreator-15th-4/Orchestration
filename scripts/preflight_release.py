"""통합·배포 전 로컬 자산, 보안 설정, 테스트와 그래프 실행을 점검한다."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag.ingest import COLLECTION_NAME, DEFAULT_PERSIST_DIR  # noqa: E402

EXPECTED_PDF_COUNTS = {
    "house_view": 6,
    "macro": 7,
    "tax": 6,
    "methodology": 2,
}
SECRET_TEMPLATE_KEYS = (
    "AZURE_OPENAI_API_KEY",
    "LANGSMITH_API_KEY",
    "RAG_INDEX_BLOB_URL",
    "RAG_INDEX_MANIFEST_URL",
)
REQUIRED_GITIGNORE_PATTERNS = frozenset({".env", "data/chroma/", "/corpus/**/*.pdf"})
OFFLINE_ENV_KEYS = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
    "LANGSMITH_API_KEY",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _result(name: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(name, "PASS" if passed else "FAIL", detail)


def corpus_pdf_counts(root: Path) -> dict[str, int]:
    return {
        category: len(list((root / "corpus" / category).glob("*.pdf")))
        for category in EXPECTED_PDF_COUNTS
    }


def _parse_env_template(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _gitignore_patterns(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def static_checks(root: Path = ROOT) -> list[CheckResult]:
    results: list[CheckResult] = []
    template = _parse_env_template(root / ".env.example")
    filled_secret_keys = [key for key in SECRET_TEMPLATE_KEYS if template.get(key, "")]
    results.append(
        _result(
            ".env.example secret placeholders",
            not filled_secret_keys,
            "API key 값 비어 있음" if not filled_secret_keys else "값이 채워진 키 존재",
        )
    )
    ignore_patterns = _gitignore_patterns(root / ".gitignore")
    missing_ignore_patterns = REQUIRED_GITIGNORE_PATTERNS.difference(ignore_patterns)
    results.append(
        _result(
            "local assets gitignore",
            not missing_ignore_patterns,
            (
                "비밀·PDF·Chroma 무시 규칙 존재"
                if not missing_ignore_patterns
                else "필수 무시 규칙 누락"
            ),
        )
    )
    secret_pattern = "lsv" + "2_|sk-" + "[A-Za-z0-9]{10,}"
    secret_scan = subprocess.run(
        ["git", "grep", "-I", "-E", secret_pattern],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if secret_scan.returncode == 1:
        secret_result = CheckResult("tracked secret pattern scan", "PASS", "의심 패턴 0건")
    elif secret_scan.returncode == 0:
        secret_result = CheckResult(
            "tracked secret pattern scan",
            "FAIL",
            "추적 파일에서 의심 패턴 발견",
        )
    else:
        secret_result = CheckResult(
            "tracked secret pattern scan",
            "FAIL",
            f"git grep 실행 실패 (exit {secret_scan.returncode})",
        )
    results.append(secret_result)
    config = yaml.safe_load((root / "config" / "config.yaml").read_text(encoding="utf-8"))
    gate_on = config.get("strict_citation_gate") is True
    results.append(
        CheckResult(
            "strict citation gate",
            "PASS" if gate_on else "WARN",
            "true" if gate_on else "개발값 false — 제출·시연 직전 true 전환 필요",
        )
    )
    return results


def local_asset_checks(root: Path = ROOT) -> list[CheckResult]:
    results: list[CheckResult] = []
    counts = corpus_pdf_counts(root)
    results.append(
        _result(
            "local corpus PDFs",
            counts == EXPECTED_PDF_COUNTS,
            f"카테고리별 {counts}, 합계 {sum(counts.values())}건",
        )
    )

    persist_dir = root / DEFAULT_PERSIST_DIR
    if not persist_dir.is_dir():
        results.append(CheckResult("Chroma index", "FAIL", "persist 디렉토리 없음"))
        return results
    try:
        from chromadb import PersistentClient

        collection = PersistentClient(path=str(persist_dir)).get_collection(COLLECTION_NAME)
        stored = collection.get(include=["metadatas"])
        metadatas = stored.get("metadatas") or []
        by_category: dict[str, set[str]] = {category: set() for category in EXPECTED_PDF_COUNTS}
        for index, metadata in enumerate(metadatas):
            if not isinstance(metadata, dict):
                raise ValueError(f"청크 {index} metadata가 dict가 아님")
            category = metadata.get("category")
            if category not in by_category:
                raise ValueError(f"청크 {index}의 예상하지 못한 category: {category}")
            source = metadata.get("source")
            if not isinstance(source, str) or not source.strip():
                raise ValueError(f"청크 {index}의 source 누락")
            by_category[category].add(source)
        sources = {source for category_sources in by_category.values() for source in category_sources}
        indexed_counts = {key: len(value) for key, value in by_category.items()}
        valid = indexed_counts == EXPECTED_PDF_COUNTS
        results.append(
            _result(
                "Chroma indexed sources",
                valid,
                f"카테고리별 {indexed_counts}, source {len(sources)}건, chunk {collection.count()}개",
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(
                "Chroma indexed sources",
                "FAIL",
                f"조회 실패: {type(exc).__name__}: {exc}",
            )
        )

    pdf_mtimes = [path.stat().st_mtime for path in (root / "corpus").glob("**/*.pdf")]
    index_mtimes = [path.stat().st_mtime for path in persist_dir.glob("**/*") if path.is_file()]
    current = bool(pdf_mtimes and index_mtimes and max(index_mtimes) >= max(pdf_mtimes))
    results.append(
        _result(
            "Chroma freshness",
            current,
            "최신 PDF 이후 인덱스 생성됨" if current else "PDF 교체 후 재인덱싱 필요",
        )
    )
    return results


def environment_checks(*, require_real: bool) -> list[CheckResult]:
    azure_keys = (
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
    )
    missing_azure = [key for key in azure_keys if not os.environ.get(key, "").strip()]
    azure_status = "FAIL" if require_real and missing_azure else "PASS" if not missing_azure else "WARN"
    results = [
        CheckResult(
            "Azure environment",
            azure_status,
            "필수 값 채워짐" if not missing_azure else "비어 있는 항목 존재(값은 출력하지 않음)",
        )
    ]
    tracing = os.environ.get("LANGSMITH_TRACING", "").strip().lower() == "true"
    langsmith_keys = ("LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT", "LANGSMITH_PROJECT")
    missing_langsmith = [key for key in langsmith_keys if not os.environ.get(key, "").strip()]
    if tracing and missing_langsmith:
        results.append(CheckResult("LangSmith environment", "FAIL", "tracing=true지만 필수 값 누락"))
    else:
        results.append(
            CheckResult(
                "LangSmith environment",
                "PASS" if tracing else "WARN",
                "APAC tracing 활성" if tracing else "tracing 비활성",
            )
        )
    return results


def offline_environment() -> dict[str, str]:
    """테스트·offline graph 자식 프로세스에서 외부 호출 자격증명을 제거한다."""
    environment = dict(os.environ)
    for key in OFFLINE_ENV_KEYS:
        environment.pop(key, None)
    environment["LANGSMITH_TRACING"] = "false"
    return environment


def command_check(
    name: str,
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    required_text: str | None = None,
) -> CheckResult:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    if completed.returncode == 0 and (
        required_text is None or required_text in completed.stdout
    ):
        return CheckResult(name, "PASS", "exit 0")
    if completed.returncode == 0 and required_text is not None:
        return CheckResult(name, "FAIL", f"필수 출력 없음: {required_text}")
    combined = (completed.stdout + "\n" + completed.stderr).strip().splitlines()
    tail = " | ".join(combined[-3:]) if combined else f"exit {completed.returncode}"
    return CheckResult(name, "FAIL", tail[:500])


def _print_results(results: list[CheckResult]) -> None:
    width = max(len(result.name) for result in results)
    for result in results:
        print(f"[{result.status:4}] {result.name:<{width}}  {result.detail}")


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="통합·배포 전 사전점검")
    parser.add_argument("--real", action="store_true", help="실제 Azure 그래프 E2E 추가")
    parser.add_argument("--skip-runtime", action="store_true", help="pytest·Ruff·graph 실행 생략")
    args = parser.parse_args()

    results = static_checks() + local_asset_checks() + environment_checks(require_real=args.real)
    if not args.skip_runtime:
        offline_env = offline_environment()
        results.extend(
            [
                command_check("Ruff", [sys.executable, "-m", "ruff", "check", "app", "scripts", "tests", "ui"]),
                command_check(
                    "pytest",
                    [sys.executable, "-m", "pytest", "-q"],
                    environment=offline_env,
                ),
                command_check(
                    "offline graph",
                    [sys.executable, "scripts/run_graph.py", "--auto-approve", "--offline"],
                    environment=offline_env,
                ),
            ]
        )
        if args.real:
            results.extend(
                [
                    command_check(
                        "four-category RAG search",
                        [sys.executable, "scripts/smoke_rag.py", "--search-only"],
                        required_text="CATEGORY_SEARCH: PASS",
                    ),
                    command_check(
                        "deployment graph E2E",
                        [
                            sys.executable,
                            "scripts/run_graph.py",
                            "--auto-approve",
                            "--validate-deployment",
                        ],
                        required_text="DEPLOYMENT_VALIDATION: PASS",
                    ),
                ]
            )

    _print_results(results)
    failures = [result for result in results if result.status == "FAIL"]
    warnings = [result for result in results if result.status == "WARN"]
    print(f"\n결과: FAIL {len(failures)}건 / WARN {len(warnings)}건 / 총 {len(results)}건")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
