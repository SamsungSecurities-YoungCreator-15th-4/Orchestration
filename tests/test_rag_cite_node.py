"""rag_cite 노드 테스트 — fake LLM/retriever 주입 (Azure/PDF/Chroma 불필요).

핵심 검증: 환각 인용이 state의 citations에 기록될 경로가 없어야 한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nodes.rag_cite import parse_candidates, rag_cite

REAL_SENTENCE = "스트레스 테스트는 역사적 VaR가 포착하지 못하는 꼬리 위험을 보완한다."


class _FakeDoc:
    def __init__(self, text: str, meta: dict):
        self.page_content = text
        self.metadata = meta


class _FakeRetriever:
    """LangChain retriever 인터페이스(invoke)만 흉내내는 순수 파이썬 fake."""

    def invoke(self, query: str):
        return [
            _FakeDoc(
                REAL_SENTENCE,
                {
                    "chunk_id": "doc_b.pdf::0003",
                    "source": "doc_b.pdf",
                    "category": "house_view",
                    "char_start": 0,
                    "char_end": len(REAL_SENTENCE),
                },
            )
        ]


class _FakeLLM:
    """실제 인용 1개 + 환각 인용 1개를 후보로 내놓는 fake."""

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


def test_only_verified_citations_recorded():
    state = {"metrics": {"var": {"0.99": 1.23}}, "judge_retries": 0}
    out = rag_cite(state, llm=_FakeLLM(), retriever=_FakeRetriever())

    citations = out["citations"]
    assert len(citations) == 1  # 환각 인용은 기록되지 않음
    assert citations[0]["quote"] == REAL_SENTENCE
    assert citations[0]["verified"] is True
    assert citations[0]["chunk_id"] == "doc_b.pdf::0003"


def test_rerun_overwrites_not_accumulates():
    state = {"metrics": {}, "judge_retries": 2, "judge_feedback": "인용-설명 연결 보강"}
    out1 = rag_cite(state, llm=_FakeLLM(), retriever=_FakeRetriever())
    out2 = rag_cite(state, llm=_FakeLLM(), retriever=_FakeRetriever())
    # 재실행해도 누적되지 않고 같은 크기로 덮어쓴다
    assert len(out1["citations"]) == len(out2["citations"]) == 1
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
    got = parse_candidates('앞말 [{"quote": "본문", "chunk_id": "a.pdf::0000"}] 뒷말', chunks)
    assert len(got) == 1 and got[0].source == "a.pdf"


def test_parse_candidates_bracket_prefix_safe():
    """LLM이 [참고] 같은 대괄호 문구를 덧붙여도 JSON 배열만 추출한다(리뷰 반영)."""
    chunks = [{"chunk_id": "a.pdf::0000", "source": "a.pdf", "text": "본문"}]
    raw = '[참고] 아래는 인용입니다.\n[{"quote": "본문", "chunk_id": "a.pdf::0000"}]\n[끝]'
    got = parse_candidates(raw, chunks)
    assert len(got) == 1
    assert got[0].quote == "본문"


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
