"""RAG 인용을 고객용 역할별 표로 변환하는 순수 UI 헬퍼."""

from __future__ import annotations

RAG_EVIDENCE_SECTIONS = (
    {
        "category": "methodology",
        "title": "정량 계산 방법론 (연산 반영)",
        "description": "사내 공식 리스크 연산 문서를 바탕으로 정량 계산되었습니다.",
    },
    {
        "category": "macro",
        "title": "거시경제 근거",
        "description": "리스크 연산을 위해 참고한 거시경제 관련 문서입니다.",
    },
    {
        "category": "house_view",
        "title": "House View 근거",
        "description": "리스크 연산을 위해 참고한 삼성증권 House View 관련 문서입니다.",
    },
    {
        "category": "tax",
        "title": "세금 이슈 근거",
        "description": "리스크 연산을 위해 참고한 국세청 세금 관련 문서입니다.",
    },
)


def group_verified_citations(citations) -> dict[str, list[dict]]:
    """검증 인용을 합의된 4개 category로 입력 순서 그대로 분류한다."""
    grouped = {section["category"]: [] for section in RAG_EVIDENCE_SECTIONS}
    if not isinstance(citations, list):
        return grouped

    for citation in citations:
        if not isinstance(citation, dict) or citation.get("verified") is not True:
            continue
        extra = citation.get("extra")
        category = extra.get("category") if isinstance(extra, dict) else None
        if category in grouped:
            grouped[category].append(citation)
    return grouped


def citation_table_rows(citations: list[object]) -> list[dict]:
    """감사용 필드를 숨기고 고객 화면의 4개 컬럼만 만든다."""
    rows: list[dict] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        extra = citation.get("extra")
        extra = extra if isinstance(extra, dict) else {}
        raw_source = citation.get("source")
        source = raw_source.strip() if isinstance(raw_source, str) else ""
        source_name = source.replace("\\", "/").rsplit("/", 1)[-1] if source else "-"
        published_at = extra.get("published_at")
        rows.append(
            {
                "설명주제": citation.get("claim") or "-",
                "근거문장": citation.get("quote") or "-",
                "출처": source_name,
                "발행기준일": (
                    published_at.strip()
                    if isinstance(published_at, str) and published_at.strip()
                    else "-"
                ),
            }
        )
    return rows
