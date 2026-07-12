"""로컬 Chroma 검색과 rag_cite 인용 검증을 실제 Azure 연결로 점검한다."""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm.client import get_llm  # noqa: E402
from app.nodes.rag_cite import rag_cite  # noqa: E402
from app.rag.retriever import build_retriever, retrieve_chunks  # noqa: E402

QUERIES = (
    ("10일 VaR은 어떻게 환산하는가", ("√t", "제곱근", "square-root")),
    ("VaR 관측 기간은 몇 거래일인가", ("1,250거래일", "1,250 거래일")),
    ("고금리 시나리오의 국내주식 충격", ("−25%", "-25%")),
)


def _snippet(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    retriever = build_retriever(k=4)

    for query, expected_terms in QUERIES:
        chunks = retrieve_chunks(retriever, query)
        methodology = [chunk for chunk in chunks if chunk["category"] == "methodology"]
        matched = [
            chunk
            for chunk in methodology
            if any(term in chunk["text"] for term in expected_terms)
        ]
        print(f"QUERY: {query}")
        for rank, chunk in enumerate(chunks, 1):
            print(
                f"  {rank}. source={chunk['source']} category={chunk['category']} "
                f"chunk_id={chunk['chunk_id']}"
            )
            print(f"     {_snippet(chunk['text'])}")
        if not methodology:
            raise RuntimeError(f"방법론 청크가 검색되지 않았습니다: {query}")
        if not matched:
            raise RuntimeError(f"기대 근거 {expected_terms}가 검색 결과에 없습니다: {query}")

    state = {
        "run_config": {"as_of_date": "2026-07-03"},
        "metrics": {
            "var": {"0.99": {"1d": 0.01, "10d": 0.0316}},
            "confidence": 0.99,
            "horizons": {
                "1d": {"var_pct": 0.01},
                "10d": {"var_pct": 0.0316},
            },
        },
        "judge_retries": 0,
    }
    result = rag_cite(state, llm=get_llm(temperature=0.0), retriever=retriever)
    verified = result["citations"]
    if any(citation.get("verified") is not True for citation in verified):
        raise RuntimeError("rag_cite가 검증되지 않은 인용을 반환했습니다.")
    print(f"RAG_CITE: citations={len(result['citations'])} verified={len(verified)}")
    for citation in verified:
        print(
            f"  source={citation['source']} chunk_id={citation['chunk_id']} "
            f"quote={_snippet(citation['quote'])}"
        )
    if not verified:
        raise RuntimeError("rag_cite에서 검증 통과 인용이 생성되지 않았습니다.")


if __name__ == "__main__":
    main()
