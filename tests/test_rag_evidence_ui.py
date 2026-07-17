"""역할별 RAG 고객 UI 변환 테스트 — Streamlit 실행 불필요."""

from ui.rag_evidence import (
    RAG_EVIDENCE_SECTIONS,
    citation_table_rows,
    group_verified_citations,
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
        "거시환경·스트레스 근거 (연산 미반영)",
        "자산시장 참고자료 (연산 미반영)",
        "세무 참고자료 (연산 미반영)",
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
