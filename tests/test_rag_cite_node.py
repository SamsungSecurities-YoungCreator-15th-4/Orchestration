"""rag_cite 노드 테스트 — fake LLM/retriever 주입 (Azure/PDF/Chroma 불필요).

핵심 검증: 환각 인용이 state의 citations에 기록될 경로가 없어야 한다.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nodes.judge_eval import judge_eval
from app.nodes.rag_cite import (
    _build_query,
    _evidence_rows,
    _select_diverse_chunks,
    _tax_issue_terms,
    _top_cvar_asset,
    parse_candidates,
    rag_cite,
)
from app.rag.citations import Citation
from app.rag.citations import verify_citations
from app.rag.ingest import CHUNK_SIZE
from app.rag.retriever import retrieve_chunks

REAL_SENTENCE = "스트레스 테스트는 역사적 VaR가 포착하지 못하는 꼬리 위험을 보완한다."


class _FakeDoc:
    def __init__(self, text: str, meta: dict):
        self.page_content = text
        self.metadata = meta


class _FakeRetriever:
    """LangChain retriever 인터페이스(invoke)만 흉내내는 순수 파이썬 fake."""

    def __init__(self):
        self.categories: list[str | None] = []

    def invoke(self, query: str, **kwargs):
        category = (kwargs.get("filter") or {}).get("category")
        self.categories.append(category)
        return [
            _FakeDoc(
                REAL_SENTENCE,
                {
                    "chunk_id": "doc_b.pdf::0003",
                    "source": "doc_b.pdf",
                    "category": category or "methodology",
                    "published_at": "2026-05-01",
                    "char_start": 0,
                    "char_end": len(REAL_SENTENCE),
                },
            )
        ]


class _FakeLLM:
    """실제 인용 1개 + 환각 인용 1개를 후보로 내놓는 fake."""

    model_name = "gpt-4o-test"
    deployment_name = "test-deployment"

    def invoke(self, prompt: str):
        return json.dumps(
            [
                {  # 원문에 실존 → 통과해야 함
                    "claim": "스트레스 테스트 보완 근거",
                    "quote": REAL_SENTENCE,
                    "chunk_id": "doc_b.pdf::0003",
                    "source": "doc_b.pdf",
                },
                {  # 환각 → 반드시 탈락해야 함
                    "claim": "수익 보장",
                    "quote": "본 전략은 연 30% 수익을 보장한다.",
                    "chunk_id": "doc_b.pdf::0003",
                    "source": "doc_b.pdf",
                },
            ],
            ensure_ascii=False,
        )


class _SearchKwargsRetriever:
    """실제 VectorStoreRetriever의 search_kwargs 계약을 흉내내는 fake."""

    def __init__(self):
        self.search_kwargs = {"k": 4}
        self.calls: list[dict] = []

    def invoke(self, query: str, **kwargs):
        self.calls.append(
            {
                "query": query,
                "search_kwargs": dict(self.search_kwargs),
                "invoke_kwargs": dict(kwargs),
            }
        )
        category = (self.search_kwargs.get("filter") or {}).get("category")
        return [
            _FakeDoc(
                REAL_SENTENCE,
                {
                    "chunk_id": "macro.pdf::0001",
                    "source": "macro_202605.pdf",
                    "category": category,
                },
            )
        ]


def test_retrieve_chunks_copies_search_kwargs_retriever_before_filtering():
    retriever = _SearchKwargsRetriever()

    chunks = retrieve_chunks(retriever, "고금리 강달러", category="macro")

    assert retriever.search_kwargs == {"k": 4}
    assert retriever.calls == [
        {
            "query": "고금리 강달러",
            "search_kwargs": {"k": 4, "filter": {"category": "macro"}},
            "invoke_kwargs": {},
        }
    ]
    assert chunks[0]["category"] == "macro"
    assert chunks[0]["published_at"] == "2026-05-01"


class _PassingJudgeLLM:
    def invoke(self, prompt: str):
        return json.dumps(
            {"passed": True, "reason": "근거·정밀도 검증 통과"},
            ensure_ascii=False,
        )


def test_only_verified_citations_recorded(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.nodes.rag_cite.annotate_current_run",
        lambda *, metadata, tags=None: captured.update(
            {"metadata": metadata, "tags": tags}
        ),
    )
    state = {
        "run_config": {"as_of_date": "2026-07-03"},
        "metrics": {"var": {"0.99": 1.23}},
        "judge_retries": 0,
    }
    out = rag_cite(state, llm=_FakeLLM(), retriever=_FakeRetriever())

    citations = out["citations"]
    assert len(citations) == 4  # 활성화된 topic 각각에서 환각 후보는 제거됨
    assert all(citation["quote"] == REAL_SENTENCE for citation in citations)
    assert all(citation["verified"] is True for citation in citations)
    assert all(citation["chunk_id"] == "doc_b.pdf::0003" for citation in citations)
    assert all(
        citation["extra"]["chunk_text"] == REAL_SENTENCE for citation in citations
    )
    assert all(citation["extra"]["published_at"] == "2026-05-01" for citation in citations)
    assert {
        citation["extra"]["evidence_role"] for citation in citations
    } == {"calculation_basis", "interpretation_reference"}
    assert all(citation["extra"]["routing_reason"] for citation in citations)
    assert {citation["claim"] for citation in citations} == {
        "VaR 해석",
        "스트레스 시나리오",
        "기준일 및 유의사항",
        "거시환경·스트레스 개연성",
    }
    disclaimer = next(
        e for e in out["explanations"] if e["topic"] == "기준일 및 유의사항"
    )
    assert "2026-07-03" in disclaimer["text"]
    assert "보장하지 않습니다" in disclaimer["text"]
    rag_audit = out["run_config"]["audit"]["llm"]["rag_cite"]["latest"]
    assert rag_audit["prompt_hash"]["aggregate_sha256"]
    assert set(rag_audit["prompt_hash"]["items"]) == {
        "VaR 해석",
        "스트레스 시나리오",
        "기준일 및 유의사항",
        "거시환경·스트레스 개연성",
    }
    assert rag_audit["model_version"]["deployment"] == "test-deployment"
    assert rag_audit["routing_contract"] == "rag-routing-v1"
    assert {route["category"] for route in rag_audit["routes"]} == {
        "methodology",
        "macro",
    }
    assert captured["metadata"]["rag_route_categories"] == "macro,methodology"
    assert captured["metadata"]["rag_evidence_roles"] == (
        "calculation_basis,interpretation_reference"
    )
    assert captured["metadata"]["rag_missing_published_at"] == 0


def test_rag_explanations_pass_judge_e2e_with_fake_llms():
    state = {
        "run_config": {
            "as_of_date": "2026-07-03",
            "strict_citation_gate": True,
        },
        "approval": {"status": "locked"},
        "metrics": {
            "confidence": 0.99,
            "horizons": {"1d": {"var_krw": 30_000_000}},
            "meta": {
                "computation_hash": "metric-hash",
                "data_period": {"end": "2026-07-03"},
            },
        },
        "judge_retries": 0,
    }
    rag_out = rag_cite(state, llm=_FakeLLM(), retriever=_FakeRetriever())
    judged = judge_eval({**state, **rag_out}, llm=_PassingJudgeLLM())

    assert judged["judge"]["passed"] is True
    assert judged["judge"]["rubric"]["disclaimer"]["passed"] is True


def test_judge_rejects_tampered_rag_routing_role():
    state = {
        "run_config": {"as_of_date": "2026-07-03", "strict_citation_gate": True},
        "approval": {"status": "locked"},
        "metrics": {
            "confidence": 0.99,
            "horizons": {"1d": {"var_krw": 30_000_000}},
            "meta": {
                "computation_hash": "metric-hash",
                "data_period": {"end": "2026-07-03"},
            },
        },
    }
    rag_out = rag_cite(state, llm=_FakeLLM(), retriever=_FakeRetriever())
    rag_out["citations"][0]["extra"]["evidence_role"] = "interpretation_reference"

    judged = judge_eval({**state, **rag_out}, llm=_PassingJudgeLLM())

    assert judged["judge"]["passed"] is False
    assert "citation_routing_contract" in {
        item["axis"] for item in json.loads(judged["judge_feedback"])["failed_axes"]
    }


def test_rerun_overwrites_not_accumulates():
    state = {"metrics": {}, "judge_retries": 2, "judge_feedback": "인용-설명 연결 보강"}
    out1 = rag_cite(state, llm=_FakeLLM(), retriever=_FakeRetriever())
    out2 = rag_cite(state, llm=_FakeLLM(), retriever=_FakeRetriever())
    # 재실행해도 누적되지 않고 같은 크기로 덮어쓴다
    assert len(out1["citations"]) == len(out2["citations"]) == 5
    # judge 피드백이 설명에 반영된다
    assert any(e["topic"] == "재작성 반영" for e in out1["explanations"])
    assert all(e["revision"] == 2 for e in out1["explanations"])


def test_fallback_without_index_returns_empty_citations():
    """인덱스·Azure 키가 없는 스켈레톤 환경에서도 노드가 완주해야 한다."""
    out = rag_cite({"metrics": {}})  # 주입 없음 → build_retriever 실패 → 폴백
    assert out["citations"] == []
    assert len(out["explanations"]) >= 2  # 결정론 설명은 유지


def test_parse_candidates_garbage_safe():
    chunks = [{"chunk_id": "a.pdf::0000", "source": "a.pdf", "text": "본문"}]
    assert parse_candidates("JSON 아님", chunks) == []
    assert parse_candidates('[{"quote": "", "chunk_id": "a.pdf::0000"}]', chunks) == []
    got = parse_candidates(
        '앞말 [{"quote": "본문", "chunk_id": "a.pdf::0000"}] 뒷말', chunks
    )
    assert len(got) == 1 and got[0].source == "a.pdf"


def test_parse_candidates_bracket_prefix_safe():
    """LLM이 [참고] 같은 대괄호 문구를 덧붙여도 JSON 배열만 추출한다(리뷰 반영)."""
    chunks = [{"chunk_id": "a.pdf::0000", "source": "a.pdf", "text": "본문"}]
    raw = '[참고] 아래는 인용입니다.\n[{"quote": "본문", "chunk_id": "a.pdf::0000"}]\n[끝]'
    got = parse_candidates(raw, chunks)
    assert len(got) == 1
    assert got[0].quote == "본문"


def test_parse_candidates_resolves_evidence_id_to_complete_pdf_sentence():
    chunks = [
        {
            "chunk_id": "methodology.pdf::0001",
            "source": "methodology.pdf",
            "text": "앞 문장의 나머지다.\n현재 포트폴리오에 독립 적용한다.\n다음 문장 일부",
            "char_start": 800,
            "char_end": 1800,
        }
    ]
    raw = json.dumps(
        [
            {
                "claim": "근거 선택",
                "evidence_id": "methodology.pdf::0001#S001",
            },
            {
                "claim": "조작된 근거",
                "evidence_id": "methodology.pdf::0001#S999",
            },
        ],
        ensure_ascii=False,
    )

    got = parse_candidates(raw, chunks)

    assert len(got) == 1
    assert got[0].quote == "현재 포트폴리오에 독립 적용한다."
    assert got[0].chunk_id == "methodology.pdf::0001"
    verified, rejected = verify_citations(got, chunks)
    assert verified == got
    assert rejected == []


def test_evidence_rows_ignore_malformed_chunks():
    chunks = [
        {},
        {"chunk_id": "none-text.pdf::0001", "text": None},
        {"chunk_id": "", "text": "빈 ID"},
        {
            "chunk_id": "valid.pdf::0001",
            "source": None,
            "text": "첫 줄입니다.\n\n둘째 줄입니다.",
        },
    ]

    assert _evidence_rows(chunks) == [
        {
            "evidence_id": "valid.pdf::0001#S001",
            "quote": "첫 줄입니다.",
            "chunk_id": "valid.pdf::0001",
            "source": "",
        },
        {
            "evidence_id": "valid.pdf::0001#S002",
            "quote": "둘째 줄입니다.",
            "chunk_id": "valid.pdf::0001",
            "source": "",
        },
    ]


def test_evidence_rows_remove_fixed_chunk_boundary_fragments():
    chunks = [
        {
            "chunk_id": "methodology.pdf::0002",
            "source": "methodology.pdf",
            "text": "앞 문장에서 잘린 조각이다.\n완전한 근거 문장입니다.\n뒤 문장에서 잘린 조각",
            "char_start": 800,
            "char_end": 1800,
        }
    ]

    assert _evidence_rows(chunks) == [
        {
            "evidence_id": "methodology.pdf::0002#S001",
            "quote": "완전한 근거 문장입니다.",
            "chunk_id": "methodology.pdf::0002",
            "source": "methodology.pdf",
        }
    ]


def test_evidence_rows_remove_single_leading_chunk_fragment():
    chunks = [
        {
            "chunk_id": "methodology.pdf::0002",
            "source": "methodology.pdf",
            "text": "이전 청크에서 시작한 문장의 남은 조각입니다.",
            "char_start": 800,
            "char_end": 840,
        }
    ]

    assert _evidence_rows(chunks) == []


def test_evidence_rows_remove_single_trailing_chunk_fragment():
    chunks = [
        {
            "chunk_id": "methodology.pdf::0001",
            "source": "methodology.pdf",
            "text": "다음 청크로 이어지는 미완성 문장 조각",
            "char_start": 0,
            "char_end": CHUNK_SIZE,
        }
    ]

    assert _evidence_rows(chunks) == []


def test_evidence_rows_skip_unreadable_unspaced_pdf_sentence():
    chunks = [
        {
            "chunk_id": "house-view.pdf::0001",
            "source": "house-view.pdf",
            "text": "본조사분석자료에수록된내용은당사리서치센터가작성했습니다.",
        },
        {
            "chunk_id": "methodology.pdf::0001",
            "source": "methodology.pdf",
            "text": "7. 표기 규약\n과거 데이터 기반 추정치는 실제 결과와 다를 수 있습니다.",
        },
    ]

    assert _evidence_rows(chunks) == [
        {
            "evidence_id": "methodology.pdf::0001#S001",
            "quote": "과거 데이터 기반 추정치는 실제 결과와 다를 수 있습니다.",
            "chunk_id": "methodology.pdf::0001",
            "source": "methodology.pdf",
        }
    ]


def test_evidence_rows_skip_table_like_oversized_sentence():
    oversized = "항목 의미 " + "신뢰구간 계산 규약 " * 30 + "."
    chunks = [
        {
            "chunk_id": "methodology.pdf::0002",
            "source": "methodology.pdf",
            "text": oversized + "\n짧고 완전한 근거 문장입니다.",
        }
    ]

    assert _evidence_rows(chunks) == [
        {
            "evidence_id": "methodology.pdf::0002#S001",
            "quote": "짧고 완전한 근거 문장입니다.",
            "chunk_id": "methodology.pdf::0002",
            "source": "methodology.pdf",
        }
    ]


def test_evidence_rows_stitch_wrapped_external_pdf_lines_without_boundary_noise():
    chunks = [
        {
            "chunk_id": "house-view.pdf::0002",
            "source": "house-view.pdf",
            "text": (
                "앞 청크에서 잘린 조각\n"
                "자료 : 삼성증권\n"
                "• 한국 주식 중에서도 고밸류에이션 종목의\n"
                "변동성이 높을 전망\n"
                "참고: 10월 기준\n"
                "다음 청크로 잘린 조각"
            ),
            "char_start": 800,
            "char_end": 1800,
        }
    ]

    assert _evidence_rows(chunks) == [
        {
            "evidence_id": "house-view.pdf::0002#S001",
            "quote": "• 한국 주식 중에서도 고밸류에이션 종목의 변동성이 높을 전망",
            "chunk_id": "house-view.pdf::0002",
            "source": "house-view.pdf",
        }
    ]


def test_evidence_rows_split_unspaced_pdf_sentences_without_breaking_decimal():
    chunks = [
        {
            "chunk_id": "macro.pdf::0001",
            "source": "macro.pdf",
            "text": (
                "금년성장률은2.0%로예상된다."
                "금융외환시장에서는주요가격변수의변동성이확대되었다."
            ),
            "char_start": 0,
            "char_end": 55,
        }
    ]

    quotes = [row["quote"] for row in _evidence_rows(chunks)]

    assert "금년성장률은2.0%로예상된다." in quotes
    assert "금융외환시장에서는주요가격변수의변동성이확대되었다." in quotes


def test_evidence_rows_do_not_split_single_letter_english_abbreviations():
    chunks = [
        {
            "chunk_id": "fomc.pdf::0001",
            "source": "fomc.pdf",
            "text": (
                "The U.S. economy remains resilient. "
                "Financial conditions have tightened."
            ),
            "char_start": 0,
            "char_end": 73,
        }
    ]

    quotes = [row["quote"] for row in _evidence_rows(chunks)]

    assert "The U.S. economy remains resilient." in quotes
    assert "The U." not in quotes
    assert "S. economy remains resilient." not in quotes


def test_evidence_rows_never_join_quote_across_removed_heading():
    chunks = [
        {
            "chunk_id": "methodology.pdf::0004",
            "source": "methodology.pdf",
            "text": "•\n6. 산출 수치의 성격과 재현성\n본 엔진은 결정론적으로 계산한다.",
            "char_start": 0,
            "char_end": 42,
        }
    ]

    quotes = [row["quote"] for row in _evidence_rows(chunks)]

    assert "• 본 엔진은 결정론적으로 계산한다." not in quotes
    assert "본 엔진은 결정론적으로 계산한다." in quotes


def test_select_diverse_chunks_caps_each_source_and_keeps_retrieval_order():
    chunks = [
        {"chunk_id": "a::1", "source": "a.pdf"},
        {"chunk_id": "a::2", "source": "a.pdf"},
        {"chunk_id": "a::3", "source": "a.pdf"},
        {"chunk_id": "b::1", "source": "b.pdf"},
        {"chunk_id": "b::2", "source": "b.pdf"},
        {"chunk_id": "c::1", "source": "c.pdf"},
        {"chunk_id": "c::2", "source": "c.pdf"},
        {"chunk_id": "d::1", "source": "d.pdf"},
    ]

    assert [chunk["chunk_id"] for chunk in _select_diverse_chunks(chunks)] == [
        "a::1",
        "a::2",
        "b::1",
        "b::2",
        "c::1",
        "c::2",
    ]


class _BrokenRetriever:
    """검색 중 네트워크류 예외를 던지는 fake."""

    def invoke(self, query: str):
        raise ConnectionError("simulated embed/chroma failure")


def test_retrieval_error_falls_back_to_empty_citations():
    """검색 단계 예외 시 그래프를 죽이지 않고 빈 인용으로 폴백한다(리뷰 반영)."""
    out = rag_cite({"metrics": {}}, llm=_FakeLLM(), retriever=_BrokenRetriever())
    assert out["citations"] == []
    assert len(out["explanations"]) >= 2


class _NoneRetriever:
    """invoke가 None을 반환하는 비정상 fake."""

    def invoke(self, query: str):
        return None


def test_none_retriever_result_falls_back():
    """retriever가 None을 반환해도 TypeError 없이 폴백한다(리뷰 반영)."""
    out = rag_cite({"metrics": {}}, llm=_FakeLLM(), retriever=_NoneRetriever())
    assert out["citations"] == []


def test_malformed_chunks_and_candidate_extra_do_not_break_rag(monkeypatch):
    valid_chunk = {
        "chunk_id": "valid.pdf::0001",
        "source": "valid.pdf",
        "category": "methodology",
        "text": REAL_SENTENCE,
    }

    monkeypatch.setattr(
        "app.rag.retriever.retrieve_chunks",
        lambda _retriever, _query, *, category=None: [
            None,
            {"chunk_id": "bad", "text": None},
            valid_chunk,
        ],
    )
    monkeypatch.setattr(
        "app.nodes.rag_cite.parse_candidates",
        lambda _raw, _chunks: [
            Citation(
                quote=REAL_SENTENCE,
                source="valid.pdf",
                chunk_id="valid.pdf::0001",
                claim="LLM claim",
                extra=None,
            )
        ],
    )

    out = rag_cite({"metrics": {}}, llm=_FakeLLM(), retriever=object())

    assert len(out["citations"]) == 3
    assert all(
        citation["extra"]["chunk_text"] == REAL_SENTENCE
        for citation in out["citations"]
    )
    assert all(
        citation["extra"]["category"] == "methodology" for citation in out["citations"]
    )
    assert {citation["claim"] for citation in out["citations"]} == {
        "VaR 해석",
        "스트레스 시나리오",
        "기준일 및 유의사항",
    }


VAR_SENTENCE = "VaR은 99% 신뢰수준과 1일 보유기간을 기준으로 산출한다."
DISCLAIMER_SENTENCE = "과거 데이터 기반 추정치는 실제 결과와 다를 수 있다."
MACRO_SENTENCE = "고금리와 강달러는 위험자산 가격의 하방 압력을 높일 수 있다."


class _TopicRetriever:
    def __init__(self):
        self.queries: list[str] = []
        self.categories: list[str | None] = []

    def invoke(self, query: str, **kwargs):
        self.queries.append(query)
        category = (kwargs.get("filter") or {}).get("category")
        self.categories.append(category)
        if category == "macro":
            return [
                _FakeDoc(
                    MACRO_SENTENCE,
                    {
                        "chunk_id": "macro_outlook_2026.pdf::0002",
                        "source": "macro_outlook_2026.pdf",
                        "category": "macro",
                    },
                )
            ]
        if "스트레스 테스트" in query:
            return [
                _FakeDoc(
                    REAL_SENTENCE,
                    {
                        "chunk_id": "methodology_stress_2026.pdf::0004",
                        "source": "methodology_stress_2026.pdf",
                        "category": "methodology",
                    },
                )
            ]
        if "Historical Simulation" in query:
            return [
                _FakeDoc(
                    VAR_SENTENCE,
                    {
                        "chunk_id": "methodology_var_cvar_2026.pdf::0003",
                        "source": "methodology_var_cvar_2026.pdf",
                        "category": "methodology",
                    },
                )
            ]
        return [
            _FakeDoc(
                DISCLAIMER_SENTENCE,
                {
                    "chunk_id": "methodology_var_cvar_2026.pdf::0009",
                    "source": "methodology_var_cvar_2026.pdf",
                    "category": "methodology",
                },
            )
        ]


class _TopicLLM:
    def __init__(self):
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        if "macro_outlook_2026.pdf::0002" in prompt:
            quote = MACRO_SENTENCE
            chunk_id = "macro_outlook_2026.pdf::0002"
            source = "macro_outlook_2026.pdf"
        elif "methodology_stress_2026.pdf::0004" in prompt:
            quote = REAL_SENTENCE
            chunk_id = "methodology_stress_2026.pdf::0004"
            source = "methodology_stress_2026.pdf"
        elif "methodology_var_cvar_2026.pdf::0003" in prompt:
            quote = VAR_SENTENCE
            chunk_id = "methodology_var_cvar_2026.pdf::0003"
            source = "methodology_var_cvar_2026.pdf"
        else:
            quote = DISCLAIMER_SENTENCE
            chunk_id = "methodology_var_cvar_2026.pdf::0009"
            source = "methodology_var_cvar_2026.pdf"
        return json.dumps(
            [
                {
                    "claim": "LLM 자유 형식 주장",
                    "quote": quote,
                    "chunk_id": chunk_id,
                    "source": source,
                }
            ],
            ensure_ascii=False,
        )


def test_topic_queries_retrieve_and_verify_independently():
    metrics = {
        "confidence": 0.99,
        "horizons": {"1d": {}, "10d": {}},
        "stress": {"A_high_rate": {}, "C_covid": {}},
        "meta": {"n_observations": 1250},
    }
    retriever = _TopicRetriever()
    llm = _TopicLLM()

    out = rag_cite({"metrics": metrics}, llm=llm, retriever=retriever)

    assert len(retriever.queries) == 4
    assert retriever.queries == [
        _build_query("VaR 해석", metrics),
        _build_query("스트레스 시나리오", metrics),
        _build_query("기준일 및 유의사항", metrics),
        _build_query("거시환경·스트레스 개연성", metrics),
    ]
    assert retriever.categories == [
        "methodology",
        "methodology",
        "methodology",
        "macro",
    ]
    assert len(set(retriever.queries)) == 4
    assert len(llm.prompts) == 4
    assert "methodology_stress_2026.pdf::0004" not in llm.prompts[0]
    assert "methodology_var_cvar_2026.pdf::0003" not in llm.prompts[1]

    by_topic = {citation["claim"]: citation for citation in out["citations"]}
    assert by_topic["VaR 해석"]["source"] == "methodology_var_cvar_2026.pdf"
    assert by_topic["스트레스 시나리오"]["source"] == "methodology_stress_2026.pdf"
    assert by_topic["거시환경·스트레스 개연성"]["source"] == "macro_outlook_2026.pdf"
    assert {citation["extra"]["category"] for citation in out["citations"]} == {
        "methodology",
        "macro",
    }


def test_category_routing_adds_house_view_and_tax_only_when_state_requires_them():
    metrics = {
        "drilldown": {
            "tail_contribution_krw": {
                "global_equity": 150_000_000,
                "domestic_equity": 200_000_000,
            }
        }
    }
    retriever = _FakeRetriever()

    out = rag_cite(
        {
            "metrics": metrics,
            "ips": {"Tax": "금융소득종합과세 대상 여부 확인 필요"},
        },
        llm=_FakeLLM(),
        retriever=retriever,
    )

    assert retriever.categories == [
        "methodology",
        "methodology",
        "methodology",
        "macro",
        "house_view",
        "tax",
    ]
    assert {citation["extra"]["category"] for citation in out["citations"]} == {
        "methodology",
        "macro",
        "house_view",
        "tax",
    }
    assert _top_cvar_asset(metrics) == "domestic_equity"
    assert _tax_issue_terms({"Tax": "금융소득종합과세 대상 여부 확인 필요"}) == (
        "금융소득",
        "종합과세",
    )
    tax_query = _build_query(
        "세무 참고",
        metrics,
        {"Tax": "금융소득종합과세 대상 여부 확인 필요"},
    )
    assert "금융소득 종합과세 비과세 분리과세" in tax_query
    assert "자영업자 포트폴리오" not in tax_query


def test_category_routing_skips_house_view_without_positive_cvar_and_tax_without_issue():
    metrics = {
        "drilldown": {
            "tail_contribution_krw": {
                "global_equity": 0,
                "domestic_equity": -1,
            }
        }
    }
    retriever = _FakeRetriever()

    out = rag_cite(
        {"metrics": metrics, "ips": {"Tax": "해당 사항 없음"}},
        llm=_FakeLLM(),
        retriever=retriever,
    )

    assert retriever.categories == [
        "methodology",
        "methodology",
        "methodology",
        "macro",
    ]
    assert _top_cvar_asset(metrics) is None
    assert _tax_issue_terms({"Tax": "해당 사항 없음"}) == ()
    assert {explanation["topic"] for explanation in out["explanations"]} == {
        "VaR 해석",
        "스트레스 시나리오",
        "기준일 및 유의사항",
        "거시환경·스트레스 개연성",
    }
