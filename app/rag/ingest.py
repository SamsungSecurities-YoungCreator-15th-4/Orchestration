"""RAG 인덱싱 배치 — corpus/ 로컬 PDF를 청킹·임베딩해 Chroma에 persist.

설계 원칙
- 결정론: chunk_id는 (파일명 + 순번)으로 고정 생성하고, 인덱스는 매 실행마다
  컬렉션을 재생성한다. 같은 코퍼스면 같은 인덱스가 나온다.
- TBD 가드: 본문에 "[TBD" 마커가 있는 미완성 방법론 문서는 인덱싱을 거부한다.
- PDF 부재 안전: 원문 PDF는 저작권상 gitignore(로컬 전용)이므로 없을 수 있다.
  없으면 스택 없이 안내 메시지를 내고 정상 종료한다.
- LangChain 표준부품만 사용(AzureOpenAIEmbeddings + Chroma). 원시 API 직접호출 금지.
  무거운 의존성(chromadb/pypdf/langchain_openai)은 함수 내부에서 지연 import 하여,
  순수 로직(가드·청킹) 테스트가 해당 패키지 없이도 동작하게 한다.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

# --- 청킹 파라미터 (상수로 분리, 값 명시) ---
CHUNK_SIZE = 1000          # 청크 문자 수
CHUNK_OVERLAP = 200        # 인접 청크 간 중첩 문자 수
TBD_MARKER = "[TBD"        # 미완성 방법론 문서 마커

# --- 인덱스 저장 규약 ---
COLLECTION_NAME = "risk_corpus"
DEFAULT_CORPUS_DIR = "corpus"
DEFAULT_PERSIST_DIR = "data/chroma"
CATEGORIES = ("house_view", "macro", "tax", "methodology")

# 임베딩: Azure OpenAI text-embedding-3-small (전용 배포명은 .env에서 읽는다)
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DEPLOYMENT_ENV = "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
EMBED_BATCH_SIZE = 64
RATE_LIMIT_WAIT_SECONDS = 65
MAX_RATE_LIMIT_RETRIES = 20
_SOURCE_YYYYMM_RE = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)")
_SOURCE_YYYY_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


# ---------------------------------------------------------------------------
# 순수 함수 (무거운 의존성 없음 — 단위 테스트 대상)
# ---------------------------------------------------------------------------
def contains_tbd(text: str) -> bool:
    """본문에 TBD 마커가 포함되어 있으면 True (인덱싱 거부 대상)."""
    return TBD_MARKER in text


def make_chunk_id(source: str, index: int) -> str:
    """파일명 + 순번으로 결정론적 chunk_id를 만든다."""
    return f"{source}::{index:04d}"


def infer_published_at(source: str) -> str:
    """파일명의 YYYYMM/연도 표기에서 결정론적 발행 기준일을 추정한다.

    코퍼스 원문에는 표준화된 발행일 metadata가 없으므로 월 단위 문서는 해당 월
    1일, 연도 단위 문서는 해당 연도 1월 1일로 기록한다. 파일명에 연도가 없으면
    빈 문자열을 반환해 Judge가 누락을 명시적으로 경고할 수 있게 한다.
    """
    if not isinstance(source, str):
        return ""
    month_match = _SOURCE_YYYYMM_RE.search(source)
    if month_match:
        return f"{month_match.group(1)}-{month_match.group(2)}-01"
    year_match = _SOURCE_YYYY_RE.search(source)
    if year_match:
        return f"{year_match.group(1)}-01-01"
    return ""


def chunk_text(
    text: str,
    source: str,
    category: str,
    published_at: str | None = None,
) -> list[dict]:
    """텍스트를 고정 파라미터로 결정론적으로 청킹한다.

    각 청크 metadata: source(파일명), category(폴더명), chunk_id, published_at,
    char_start, char_end.
    """
    step = CHUNK_SIZE - CHUNK_OVERLAP
    if step <= 0:
        raise ValueError("CHUNK_SIZE는 CHUNK_OVERLAP보다 커야 한다.")

    chunks: list[dict] = []
    n = len(text)
    start = 0
    idx = 0
    while start < n:
        end = min(start + CHUNK_SIZE, n)
        piece = text[start:end]
        if piece.strip():
            chunks.append(
                {
                    "chunk_id": make_chunk_id(source, idx),
                    "text": piece,
                    "source": source,
                    "category": category,
                    "published_at": (
                        published_at
                        if isinstance(published_at, str)
                        else infer_published_at(source)
                    ),
                    "char_start": start,
                    "char_end": end,
                }
            )
            idx += 1
        if end >= n:
            break
        start += step
    return chunks


def partition_documents(named_texts: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[dict]]:
    """(파일명, 본문) 목록을 인덱싱 가능/거부로 분리한다.

    반환: (accepted[(name, text)], skipped[{"source", "reason"}])
    TBD 마커 문서와 빈 문서는 거부한다.
    """
    accepted: list[tuple[str, str]] = []
    skipped: list[dict] = []
    for name, text in named_texts:
        if contains_tbd(text):
            skipped.append({"source": name, "reason": "TBD 마커 포함 — 미완성 문서"})
        elif not text.strip():
            skipped.append({"source": name, "reason": "빈 문서"})
        else:
            accepted.append((name, text))
    return accepted, skipped


# ---------------------------------------------------------------------------
# 무거운 의존성 (지연 import)
# ---------------------------------------------------------------------------
def load_pdf_text(path: Path) -> str:
    """PDF 전체 페이지 텍스트를 합쳐 반환 (pypdf 지연 import)."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def build_embedder():
    """AzureOpenAIEmbeddings 인스턴스 생성 (지연 import, 키는 .env에서만 읽음)."""
    required = ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", EMBEDDING_DEPLOYMENT_ENV]
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        raise RuntimeError(
            "임베딩에 필요한 환경 변수가 없습니다: " + ", ".join(missing)
        )

    from langchain_openai import AzureOpenAIEmbeddings

    return AzureOpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        azure_deployment=os.environ[EMBEDDING_DEPLOYMENT_ENV],
    )


def is_rate_limit_error(exc: Exception) -> bool:
    """openai.RateLimitError 여부를 지연 import로 판별한다."""
    try:
        from openai import RateLimitError
    except ImportError:
        return False
    return isinstance(exc, RateLimitError)


def collect_corpus_texts(corpus_dir: str = DEFAULT_CORPUS_DIR) -> list[tuple[str, str, str]]:
    """카테고리 폴더의 PDF를 정렬된 순서로 로드한다.

    반환: [(파일명, 본문, category)] — 결정론적 순서(sorted).
    """
    base = Path(corpus_dir)
    out: list[tuple[str, str, str]] = []
    for category in CATEGORIES:
        cat_dir = base / category
        if not cat_dir.is_dir():
            continue
        for pdf in sorted(cat_dir.glob("*.pdf")):
            try:
                text = load_pdf_text(pdf)
            except Exception as e:  # 손상·암호화 PDF — 배치 전체를 죽이지 않고 건너뜀
                log.error("PDF 로드 실패(건너뜀): %s — %s", pdf.name, e)
                continue
            out.append((pdf.name, text, category))
    return out


def _chunk_metadata(chunk: dict) -> dict:
    metadata = {
        "source": chunk["source"],
        "category": chunk["category"],
        "chunk_id": chunk["chunk_id"],
        "char_start": chunk["char_start"],
        "char_end": chunk["char_end"],
    }
    published_at = chunk.get("published_at")
    if isinstance(published_at, str) and published_at:
        metadata["published_at"] = published_at
    return metadata


def add_chunks_with_retries(
    store,
    chunks: list[dict],
    *,
    batch_size: int = EMBED_BATCH_SIZE,
    wait_seconds: int = RATE_LIMIT_WAIT_SECONDS,
    max_retries: int = MAX_RATE_LIMIT_RETRIES,
) -> None:
    """Chroma add_texts를 배치 단위로 호출하고 429는 같은 배치를 재시도한다.

    임베딩 호출은 Chroma/LangChain 내부의 embedding_function 경유로만 발생한다.
    여기서는 RateLimitError를 예외 식별에만 사용하고 원시 API를 직접 호출하지 않는다.
    """
    if batch_size <= 0:
        raise ValueError("batch_size는 1 이상이어야 합니다.")

    total_batches = (len(chunks) + batch_size - 1) // batch_size
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        batch_no = start // batch_size + 1
        retries = 0
        while True:
            try:
                log.info(
                    "임베딩 배치 추가: %d/%d (%d청크)",
                    batch_no,
                    total_batches,
                    len(batch),
                )
                store.add_texts(
                    texts=[c["text"] for c in batch],
                    metadatas=[_chunk_metadata(c) for c in batch],
                    ids=[c["chunk_id"] for c in batch],  # 결정론적 id → 재실행 시 동일 인덱스
                )
                break
            except Exception as e:
                if not is_rate_limit_error(e):
                    raise
                retries += 1
                if retries > max_retries:
                    raise RuntimeError(
                        "Azure OpenAI RateLimitError 재시도 한도를 초과했습니다: "
                        f"batch={batch_no}/{total_batches}, "
                        f"max_retries={max_retries}, wait_seconds={wait_seconds}"
                    ) from e
                log.warning(
                    "RateLimitError 발생: 배치 %d/%d, 재시도 %d/%d. %d초 대기 후 재시도합니다.",
                    batch_no,
                    total_batches,
                    retries,
                    max_retries,
                    wait_seconds,
                )
                time.sleep(wait_seconds)


def build_index(
    corpus_dir: str = DEFAULT_CORPUS_DIR,
    persist_dir: str = DEFAULT_PERSIST_DIR,
    embedder=None,
) -> dict:
    """corpus를 청킹·임베딩해 Chroma에 persist. 요약 dict를 반환한다."""
    loaded = collect_corpus_texts(corpus_dir)
    if not loaded:
        log.warning(
            "corpus/ 하위에서 PDF를 찾지 못했습니다. 원문 PDF는 저작권상 gitignore(로컬 전용)"
            "이므로, 로컬에 PDF를 배치한 뒤 다시 실행하세요. (인덱스를 만들지 않고 종료)"
        )
        return {"indexed_chunks": 0, "indexed_docs": 0, "skipped": [], "reason": "no_pdf"}

    named_texts = [(name, text) for name, text, _cat in loaded]
    category_by_name = {name: cat for name, _text, cat in loaded}
    accepted, skipped = partition_documents(named_texts)

    for s in skipped:
        log.warning("인덱싱 거부: %s — %s", s["source"], s["reason"])

    all_chunks: list[dict] = []
    for name, text in accepted:
        all_chunks.extend(
            chunk_text(
                text,
                source=name,
                category=category_by_name[name],
                published_at=infer_published_at(name),
            )
        )

    if not all_chunks:
        log.warning("인덱싱할 청크가 없습니다(모든 문서가 거부되었을 수 있음).")
        return {"indexed_chunks": 0, "indexed_docs": 0, "skipped": skipped, "reason": "no_chunk"}

    if embedder is None:
        embedder = build_embedder()

    from langchain_chroma import Chroma

    # 결정론: 기존 컬렉션 디렉터리를 지우고 재생성한다.
    if Path(persist_dir).exists():
        shutil.rmtree(persist_dir)
    Path(persist_dir).mkdir(parents=True, exist_ok=True)

    store = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embedder,
        persist_directory=persist_dir,
    )
    add_chunks_with_retries(store, all_chunks)

    log.info("인덱싱 완료: 문서 %d건, 청크 %d개 → %s", len(accepted), len(all_chunks), persist_dir)
    return {
        "indexed_chunks": len(all_chunks),
        "indexed_docs": len(accepted),
        "skipped": skipped,
        "reason": "ok",
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="RAG 코퍼스 인덱싱")
    parser.add_argument("--corpus-dir", default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--persist-dir", default=DEFAULT_PERSIST_DIR)
    args = parser.parse_args()

    summary = build_index(corpus_dir=args.corpus_dir, persist_dir=args.persist_dir)
    print(
        f"[ingest] reason={summary['reason']} "
        f"docs={summary['indexed_docs']} chunks={summary['indexed_chunks']} "
        f"skipped={len(summary['skipped'])}"
    )
    for s in summary["skipped"]:
        print(f"  - skipped: {s['source']} ({s['reason']})")


if __name__ == "__main__":
    main()
