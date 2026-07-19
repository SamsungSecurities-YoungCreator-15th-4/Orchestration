"""역할별 RAG 고객 UI 변환 테스트 — Streamlit 실행 불필요."""

from ui.rag_evidence import (
    RAG_EVIDENCE_SECTIONS,
    citation_table_rows,
    group_verified_citations,
    partition_methodology_citations,
    reference_document_counts,
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
        "정량 계산 방법론 (계산 근거)",
        "거시환경·스트레스 근거 (참고용 · 계산 근거 아님)",
        "자산시장 참고자료 (참고용 · 계산 근거 아님)",
        "세무 참고자료 (참고용 · 계산 근거 아님)",
    ]


def test_evidence_section_copy_matches_evidence_role_contract():
    methodology, *references = RAG_EVIDENCE_SECTIONS

    assert "계산 근거" in methodology["title"]
    assert "계산 근거 아님" not in methodology["title"]
    assert all("계산 근거 아님" in section["title"] for section in references)
    assert all("참고 문서" in section["description"] for section in references)
    assert all("리스크 연산" not in section["description"] for section in references)


def test_reference_document_counts_exclude_methodology_and_duplicate_chunks():
    macro = _citation("macro")
    macro_duplicate = _citation("macro")
    macro_duplicate["chunk_id"] = "macro_202605.pdf::0002"
    house_view = _citation("house_view")
    tax_unverified = _citation("tax", verified=False)

    counts = reference_document_counts(
        [
            _citation("methodology"),
            macro,
            macro_duplicate,
            house_view,
            tax_unverified,
            _citation("unknown"),
            {"verified": True, "source": None, "extra": {"category": "macro"}},
            None,
        ]
    )

    assert counts == {"total": 3, "verified": 2}
    assert reference_document_counts(None) == {"total": 0, "verified": 0}


def test_methodology_citations_are_partitioned_by_stress_source():
    quantitative = _citation("methodology")
    quantitative["source"] = "/private/corpus/methodology_var_cvar_2026.pdf"
    stress = _citation("methodology")
    stress["source"] = "/private/corpus/methodology_stress_2026.pdf"

    quantitative_rows, stress_rows = partition_methodology_citations(
        [quantitative, None, stress]
    )

    assert quantitative_rows == [quantitative]
    assert stress_rows == [stress]
    assert partition_methodology_citations(None) == ([], [])


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
