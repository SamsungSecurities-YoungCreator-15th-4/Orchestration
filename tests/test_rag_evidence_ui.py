"""역할별 RAG 고객 UI 변환 테스트 — Streamlit 실행 불필요."""

from ui.rag_evidence import (
    RAG_EVIDENCE_SECTIONS,
    citation_table_rows,
    group_verified_citations,
    replace_citation_indexes,
    unique_review_warnings,
)


def _citation(category: str, *, verified: bool = True) -> dict:
    return {
        "claim": f"{category} 설명",
        "quote": f"{category} 근거 문장",
        "source": f"/private/corpus/{category}_202605.pdf",
        "chunk_id": f"{category}_202605.pdf::0001",
        "verified": verified,
        "extra": {
            "category": category,
            "evidence_role": (
                "calculation_basis"
                if category == "methodology"
                else "interpretation_reference"
            ),
            "routing_reason": f"{category} route",
            "published_at": "2026-05-01",
        },
    }


def test_verified_citations_are_grouped_into_four_agreed_sections():
    citations = [
        _citation("macro"),
        _citation("methodology"),
        _citation("house_view"),
        _citation("tax"),
        _citation("macro", verified=False),
        _citation("unknown"),
        None,
    ]

    grouped = group_verified_citations(citations)

    assert list(grouped) == [
        "methodology",
        "macro",
        "house_view",
        "tax",
    ]
    assert [len(grouped[category]) for category in grouped] == [1, 1, 1, 1]
    assert [section["title"] for section in RAG_EVIDENCE_SECTIONS] == [
        "정량 계산 방법론 (연산 반영)",
        "거시경제 근거",
        "House View 근거",
        "세금 이슈 근거",
    ]


def test_customer_rows_expose_only_agreed_fields():
    rows = citation_table_rows([None, "invalid", _citation("macro")])

    assert rows == [
        {
            "설명주제": "macro 설명",
            "근거문장": "macro 근거 문장",
            "출처": "macro_202605.pdf",
            "발행기준일": "2026-05-01",
        }
    ]
    assert "category" not in rows[0]
    assert "evidence_role" not in rows[0]
    assert "routing_reason" not in rows[0]


def test_missing_publication_date_and_source_have_safe_placeholders():
    citation = _citation("tax")
    citation["source"] = None
    citation["extra"]["published_at"] = ""

    assert citation_table_rows([citation])[0] == {
        "설명주제": "tax 설명",
        "근거문장": "tax 근거 문장",
        "출처": "-",
        "발행기준일": "-",
    }


def test_freshness_warning_uses_document_names_and_removes_chunk_duplicates():
    first = _citation("house_view")
    duplicate = _citation("house_view")
    duplicate["chunk_id"] = "house_view_202605.pdf::0002"
    second = _citation("house_view")
    second["source"] = "/private/corpus/house_view_202604.pdf"
    second["chunk_id"] = "house_view_202604.pdf::0001"
    citations = [first, duplicate, second]
    warnings = [
        "#1 house_view 8개월 경과 — 최신성 경고, "
        "#2 house_view 8개월 경과 — 최신성 경고, "
        "#3 house_view 9개월 경과 — 최신성 경고"
    ]

    assert unique_review_warnings(warnings, citations) == [
        "house_view_202605.pdf — 8개월 경과 — 최신성 경고",
        "house_view_202604.pdf — 9개월 경과 — 최신성 경고",
    ]
    assert replace_citation_indexes(warnings[0], citations).startswith(
        "house_view_202605.pdf — 8개월 경과"
    )


def test_citation_index_replacement_ignores_malformed_verified_citations():
    citations = [
        {"verified": True, "quote": "근거", "source": None, "chunk_id": "chunk-1"},
        {"verified": True, "quote": 123, "source": "bad.pdf", "chunk_id": "chunk-2"},
        _citation("house_view"),
    ]

    assert replace_citation_indexes("#1 house_view 8개월 경과", citations) == (
        "house_view_202605.pdf — 8개월 경과"
    )
