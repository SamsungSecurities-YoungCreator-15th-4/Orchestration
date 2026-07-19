"""RAG 인용을 고객용 역할별 표로 변환하는 순수 UI 헬퍼."""

from __future__ import annotations

import re

RAG_EVIDENCE_SECTIONS = (
    {
        "category": "methodology",
        "title": "정량 계산 방법론 (계산 근거)",
        "description": "정량 엔진에 적용된 VaR·CVaR·스트레스 계산 방법론 문서입니다.",
    },
    {
        "category": "macro",
        "title": "거시환경·스트레스 근거 (참고용 · 계산 근거 아님)",
        "description": "스트레스 시나리오의 거시적 개연성을 해석하기 위한 참고 문서입니다.",
    },
    {
        "category": "house_view",
        "title": "자산시장 참고자료 (참고용 · 계산 근거 아님)",
        "description": "포트폴리오 결과의 자산시장 위험을 해석하기 위한 참고 문서입니다.",
    },
    {
        "category": "tax",
        "title": "세무 참고자료 (참고용 · 계산 근거 아님)",
        "description": "IPS에서 확인된 세무 이슈를 해석하기 위한 참고 문서입니다.",
    },
)

REFERENCE_EVIDENCE_CATEGORIES = frozenset({"macro", "house_view", "tax"})


def reference_document_counts(citations: object) -> dict[str, int]:
    """방법론을 제외한 참고 근거의 고유 전체·검증 문서 수를 반환한다."""

    all_sources: set[str] = set()
    verified_sources: set[str] = set()
    if not isinstance(citations, list):
        return {"total": 0, "verified": 0}

    for citation in citations:
        if not isinstance(citation, dict):
            continue
        extra = citation.get("extra")
        category = extra.get("category") if isinstance(extra, dict) else None
        source = citation.get("source")
        if category not in REFERENCE_EVIDENCE_CATEGORIES or not isinstance(source, str):
            continue
        source_name = source.strip().replace("\\", "/").rsplit("/", 1)[-1]
        if not source_name:
            continue
        all_sources.add(source_name)
        if citation.get("verified") is True:
            verified_sources.add(source_name)

    return {"total": len(all_sources), "verified": len(verified_sources)}


def partition_methodology_citations(
    citations: object,
) -> tuple[list[dict], list[dict]]:
    """방법론 인용을 VaR/CVaR용과 스트레스 테스트용으로 순서 보존 분리한다."""

    quantitative: list[dict] = []
    stress: list[dict] = []
    if not isinstance(citations, list):
        return quantitative, stress

    for citation in citations:
        if not isinstance(citation, dict):
            continue
        source = citation.get("source")
        source_name = (
            source.strip().replace("\\", "/").rsplit("/", 1)[-1].casefold()
            if isinstance(source, str)
            else ""
        )
        if "stress" in source_name:
            stress.append(citation)
        else:
            quantitative.append(citation)
    return quantitative, stress


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


def replace_citation_indexes(text: object, citations: object) -> str:
    """Judge의 ``#N house_view`` 표기를 해당 검증 인용의 문서명으로 바꾼다."""

    value = str(text or "")
    verified_sources: list[str] = []
    if isinstance(citations, list):
        for citation in citations:
            if not isinstance(citation, dict) or citation.get("verified") is not True:
                continue
            quote = citation.get("quote")
            source = citation.get("source")
            chunk_id = citation.get("chunk_id")
            if not (
                isinstance(quote, str)
                and quote.strip()
                and isinstance(source, str)
                and source.strip()
                and isinstance(chunk_id, str)
                and chunk_id.strip()
            ):
                continue
            source_name = source.replace("\\", "/").rsplit("/", 1)[-1]
            verified_sources.append(source_name)

    def _replace(match: re.Match[str]) -> str:
        index = int(match.group("index"))
        if 1 <= index <= len(verified_sources):
            return f"{verified_sources[index - 1]} —"
        return match.group(0)

    return re.sub(r"#(?P<index>\d+)(?:\s+house_view)?", _replace, value)


def unique_review_warnings(warnings: object, citations: object) -> list[str]:
    """합쳐진 Judge 경고를 문서명으로 바꾸고 같은 문서 경고를 한 번만 남긴다."""

    if not isinstance(warnings, list):
        return []

    items: list[str] = []
    for warning in warnings:
        items.extend(
            part.strip()
            for part in re.split(r",\s(?=#)", str(warning))
            if part.strip()
        )
    display_items = [replace_citation_indexes(item, citations) for item in items]
    return list(dict.fromkeys(display_items))
