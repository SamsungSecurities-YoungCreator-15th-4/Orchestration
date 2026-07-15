"""런타임 검색 — persist된 Chroma를 로드해 LangChain retriever를 구성.

임베딩·벡터스토어는 LangChain 표준부품(AzureOpenAIEmbeddings / Chroma)만 경유한다.
검색 결과는 청크 원문 + metadata를 온전히 보존해 반환한다
(citations.verify_citations 검증에 원문이 필요하다).
"""

from __future__ import annotations

from pathlib import Path

from app.rag.ingest import (
    CATEGORIES,
    COLLECTION_NAME,
    DEFAULT_PERSIST_DIR,
    build_embedder,
)

DEFAULT_TOP_K = 4  # 반환 청크 수 기본값


def index_exists(persist_dir: str = DEFAULT_PERSIST_DIR) -> bool:
    """persist된 Chroma 인덱스 디렉터리가 존재하는지."""
    p = Path(persist_dir)
    return p.is_dir() and any(p.iterdir())


def build_retriever(
    persist_dir: str = DEFAULT_PERSIST_DIR,
    k: int = DEFAULT_TOP_K,
    embedder=None,
):
    """persist된 Chroma를 로드해 LangChain retriever를 반환하는 팩토리.

    embedder를 주입하면(테스트용 fake 등) Azure 환경 변수 없이도 구성 가능하다.
    """
    if not index_exists(persist_dir):
        raise FileNotFoundError(
            f"Chroma 인덱스가 없습니다: {persist_dir} — 먼저 "
            "`python -m app.rag.ingest` 로 인덱싱을 실행하세요."
        )
    if embedder is None:
        embedder = build_embedder()

    from langchain_chroma import Chroma

    store = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embedder,
        persist_directory=persist_dir,
    )
    return store.as_retriever(search_kwargs={"k": k})


def retrieve_chunks(
    retriever,
    query: str,
    *,
    category: str | None = None,
) -> list[dict]:
    """retriever로 검색해 청크 원문+metadata를 보존한 dict 목록으로 반환.

    각 항목: {"text", "chunk_id", "source", "category", "char_start", "char_end"}
    (metadata 키가 없으면 빈 값으로 채운다 — 검증 로직이 KeyError 없이 동작하도록.)

    category가 주어지면 Chroma metadata filter를 검색 시점에 적용한다. 검색 뒤
    노드 계층에서도 category를 다시 검증하므로, 벡터 검색 구현이 filter 계약을
    어기더라도 다른 역할의 문서가 인용 후보로 섞이지 않는다.
    """
    if category is not None and category not in CATEGORIES:
        raise ValueError(
            f"지원하지 않는 RAG category입니다: {category!r} (허용값: {CATEGORIES})"
        )
    invoke_kwargs = {"filter": {"category": category}} if category else {}
    docs = retriever.invoke(query, **invoke_kwargs) or []
    out: list[dict] = []
    for d in docs:
        md = d.metadata or {}
        out.append(
            {
                "text": d.page_content,
                "chunk_id": md.get("chunk_id", ""),
                "source": md.get("source", ""),
                "category": md.get("category", ""),
                "char_start": md.get("char_start"),
                "char_end": md.get("char_end"),
            }
        )
    return out
