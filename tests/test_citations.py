"""인용 검증(순수 결정론) 단위 테스트 — Azure/PDF 불필요."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.citations import Citation, normalize_ws, verify_citations

CHUNKS = [
    {
        "chunk_id": "doc_a.pdf::0000",
        "source": "doc_a.pdf",
        "text": "99% 1일 VaR는 정상 시장에서 하루 동안 발생할 수 있는\n최대 손실의 통계적 추정치이다.",
    },
    {
        "chunk_id": "doc_b.pdf::0003",
        "source": "doc_b.pdf",
        "text": "스트레스 테스트는 역사적 VaR가 포착하지 못하는 꼬리 위험을 보완한다.",
    },
]


def _make(quote: str, chunk_id: str) -> Citation:
    return Citation(quote=quote, source=chunk_id.split("::")[0], chunk_id=chunk_id)


def test_quote_in_source_passes():
    cits = [_make("최대 손실의 통계적 추정치이다.", "doc_a.pdf::0000")]
    verified, rejected = verify_citations(cits, CHUNKS)
    assert len(verified) == 1 and verified[0].verified is True
    assert rejected == []


def test_hallucinated_quote_rejected():
    cits = [_make("본 상품은 원금이 보장되며 연 20% 수익이 확정된다.", "doc_a.pdf::0000")]
    verified, rejected = verify_citations(cits, CHUNKS)
    assert verified == []
    assert len(rejected) == 1
    assert "원문에 없음" in rejected[0]["reason"]


def test_unknown_chunk_id_rejected():
    cits = [_make("최대 손실의 통계적 추정치이다.", "ghost.pdf::9999")]
    verified, rejected = verify_citations(cits, CHUNKS)
    assert verified == []
    assert "존재하지 않는 chunk_id" in rejected[0]["reason"]


def test_whitespace_difference_passes():
    # 원문은 중간에 개행 — 인용은 단일 공백. 공백 정규화로 통과해야 한다.
    cits = [_make("발생할 수 있는 최대 손실의 통계적 추정치이다.", "doc_a.pdf::0000")]
    verified, rejected = verify_citations(cits, CHUNKS)
    assert len(verified) == 1
    assert rejected == []


def test_empty_quote_rejected():
    cits = [_make("   ", "doc_a.pdf::0000")]
    verified, rejected = verify_citations(cits, CHUNKS)
    assert verified == []
    assert rejected[0]["reason"] == "빈 인용문"


def test_reproducible_same_input_same_output():
    def run():
        cits = [
            _make("최대 손실의 통계적 추정치이다.", "doc_a.pdf::0000"),
            _make("존재하지 않는 문장", "doc_b.pdf::0003"),
        ]
        verified, rejected = verify_citations(cits, CHUNKS)
        return ([c.to_dict() for c in verified], rejected)

    assert run() == run()  # 같은 입력 2회 → 동일 결과


def test_normalize_ws():
    assert normalize_ws("a \n  b\t c") == "a b c"
