"""로컬 Chroma 검색과 rag_cite 인용 검증을 실제 Azure 연결로 점검한다."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm.client import get_llm  # noqa: E402
from app.nodes.rag_cite import rag_cite  # noqa: E402
from app.rag.retriever import build_retriever, retrieve_chunks  # noqa: E402

EXPECTED_CATEGORIES = frozenset({"methodology", "macro", "house_view", "tax"})
QUERIES = (
    (
        "methodology",
        "10일 VaR은 어떻게 환산하는가",
        ("√t", "제곱근", "square-root"),
    ),
    (
        "methodology",
        "VaR 관측 기간은 몇 거래일인가",
        ("1,250거래일", "1,250 거래일"),
    ),
    (
        "methodology",
        "고금리 시나리오의 국내주식 충격",
        ("−25%", "-25%"),
    ),
    ("macro", "한국 기준금리 원달러 환율과 금융시장 하방 위험", ()),
    ("house_view", "KOSPI 국내주식 시장 전망과 변동성 하방 위험", ()),
    ("tax", "금융소득 종합과세 이자 배당소득 세후 유동성", ()),
)


def _snippet(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    parser = argparse.ArgumentParser(description="4개 category RAG 실제 검색 점검")
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="Azure LLM 인용 생성 없이 category별 Chroma 검색만 확인",
    )
    args = parser.parse_args()
    retriever = build_retriever(k=4)

    searched_categories: set[str] = set()
    for category, query, expected_terms in QUERIES:
        chunks = retrieve_chunks(retriever, query, category=category)
        matching_category = [
            chunk for chunk in chunks if chunk["category"] == category
        ]
        matched = [
            chunk for chunk in matching_category
            if any(term in chunk["text"] for term in expected_terms)
        ]
        print(f"QUERY: category={category} text={query}")
        for rank, chunk in enumerate(chunks, 1):
            print(
                f"  {rank}. source={chunk['source']} category={chunk['category']} "
                f"chunk_id={chunk['chunk_id']}"
            )
            print(f"     {_snippet(chunk['text'])}")
        if not matching_category:
            raise RuntimeError(f"{category} 청크가 검색되지 않았습니다: {query}")
        if len(matching_category) != len(chunks):
            raise RuntimeError(f"{category} 외 category 청크가 섞였습니다: {query}")
        if expected_terms and not matched:
            raise RuntimeError(f"기대 근거 {expected_terms}가 검색 결과에 없습니다: {query}")
        searched_categories.add(category)

    missing_categories = EXPECTED_CATEGORIES - searched_categories
    if missing_categories:
        raise RuntimeError("검색하지 못한 category: " + ", ".join(sorted(missing_categories)))
    print("CATEGORY_SEARCH: PASS categories=" + ",".join(sorted(searched_categories)))

    if args.search_only:
        return

    state = {
        "run_config": {
            "as_of_date": "2026-07-03",
            "strict_citation_gate": True,
        },
        "ips": {"Tax": "금융소득 종합과세 및 이자·배당소득 확인 필요"},
        "metrics": {
            "var": {"0.99": {"1d": 0.01, "10d": 0.0316}},
            "confidence": 0.99,
            "horizons": {
                "1d": {"var_pct": 0.01},
                "10d": {"var_pct": 0.0316},
            },
            "drilldown": {
                "tail_contribution_krw": {
                    "domestic_equity": 70_000_000,
                    "domestic_bond": 10_000_000,
                }
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
    citation_categories = {
        (citation.get("extra") or {}).get("category") for citation in verified
    }
    missing_citations = EXPECTED_CATEGORIES - citation_categories
    if missing_citations:
        raise RuntimeError(
            "검증 인용이 없는 category: " + ", ".join(sorted(missing_citations))
        )


if __name__ == "__main__":
    main()
