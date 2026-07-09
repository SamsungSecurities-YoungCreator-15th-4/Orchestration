"""인덱싱 가드·청킹 결정론 테스트 — 실제 PDF/Azure 불필요(순수 함수 수준)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.ingest import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    build_index,
    chunk_text,
    contains_tbd,
    make_chunk_id,
    partition_documents,
)


def test_tbd_marker_detected():
    assert contains_tbd("방법론: [TBD — VaR 산출 기준 미확정]") is True
    assert contains_tbd("완성된 방법론 문서") is False


def test_partition_rejects_tbd_document():
    docs = [
        ("ok.pdf", "완성된 본문"),
        ("draft.pdf", "서론… [TBD 나머지는 추후 작성]"),
    ]
    accepted, skipped = partition_documents(docs)
    assert [name for name, _ in accepted] == ["ok.pdf"]
    assert len(skipped) == 1
    assert skipped[0]["source"] == "draft.pdf"
    assert "TBD" in skipped[0]["reason"]


def test_partition_rejects_empty_document():
    accepted, skipped = partition_documents([("empty.pdf", "   \n ")])
    assert accepted == []
    assert skipped[0]["reason"] == "빈 문서"


def test_chunk_id_deterministic_format():
    assert make_chunk_id("a.pdf", 7) == "a.pdf::0007"


def test_chunk_text_deterministic_and_metadata():
    text = "가나다라" * 700  # CHUNK_SIZE보다 충분히 긴 텍스트
    c1 = chunk_text(text, source="x.pdf", category="macro")
    c2 = chunk_text(text, source="x.pdf", category="macro")
    assert c1 == c2  # 같은 입력 2회 → 동일 청크(재현성)
    assert len(c1) >= 2
    first = c1[0]
    assert first["chunk_id"] == "x.pdf::0000"
    assert first["source"] == "x.pdf"
    assert first["category"] == "macro"
    assert first["char_start"] == 0
    assert first["char_end"] == CHUNK_SIZE
    # 중첩 검증: 다음 청크 시작 = CHUNK_SIZE - CHUNK_OVERLAP
    assert c1[1]["char_start"] == CHUNK_SIZE - CHUNK_OVERLAP


def test_chunk_text_roundtrip_substring():
    text = "동해물과 백두산이 마르고 닳도록 " * 200
    for c in chunk_text(text, source="y.pdf", category="tax"):
        assert c["text"] == text[c["char_start"]:c["char_end"]]


def test_build_index_no_pdf_exits_cleanly(tmp_path):
    summary = build_index(corpus_dir=str(tmp_path), persist_dir=str(tmp_path / "chroma"))
    assert summary == {
        "indexed_chunks": 0,
        "indexed_docs": 0,
        "skipped": [],
        "reason": "no_pdf",
    }


def test_corrupted_pdf_skipped_not_fatal(tmp_path, monkeypatch):
    """손상 PDF 하나가 배치 전체를 중단시키지 않는다(리뷰 반영)."""
    import app.rag.ingest as ingest

    cat_dir = tmp_path / "macro"
    cat_dir.mkdir()
    (cat_dir / "bad.pdf").write_bytes(b"not a pdf")
    (cat_dir / "good.pdf").write_bytes(b"not a pdf either")

    def fake_load(path):
        if path.name == "bad.pdf":
            raise ValueError("simulated corrupted pdf")
        return "정상 본문"

    monkeypatch.setattr(ingest, "load_pdf_text", fake_load)
    loaded = ingest.collect_corpus_texts(corpus_dir=str(tmp_path))
    assert [(name, cat) for name, _text, cat in loaded] == [("good.pdf", "macro")]
