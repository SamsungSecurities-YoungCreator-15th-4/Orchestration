"""인덱싱 가드·청킹 결정론 테스트 — 실제 PDF/Azure 불필요(순수 함수 수준)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.ingest import (
    CATEGORIES,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBED_BATCH_SIZE,
    MAX_RATE_LIMIT_RETRIES,
    RATE_LIMIT_WAIT_SECONDS,
    add_chunks_with_retries,
    build_index,
    chunk_text,
    contains_tbd,
    make_chunk_id,
    partition_documents,
)


def _make_chunks(n: int) -> list[dict]:
    return [
        {
            "chunk_id": make_chunk_id("bulk.pdf", i),
            "text": f"청크 본문 {i}",
            "source": "bulk.pdf",
            "category": "macro",
            "char_start": i * 10,
            "char_end": i * 10 + 7,
        }
        for i in range(n)
    ]


def _rate_limit_error():
    import httpx
    from openai import RateLimitError

    request = httpx.Request("POST", "https://example.test/embeddings")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


class _FakeEmbedder:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls = 0
        self.batch_sizes: list[int] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.batch_sizes.append(len(texts))
        if self.calls <= self.failures:
            raise _rate_limit_error()
        return [[0.0] for _ in texts]


class _FakeStore:
    def __init__(self, embedder: _FakeEmbedder):
        self.embedder = embedder
        self.ids_by_call: list[list[str]] = []

    def add_texts(self, *, texts: list[str], metadatas: list[dict], ids: list[str]):
        assert len(texts) == len(metadatas) == len(ids)
        self.embedder.embed_documents(texts)
        self.ids_by_call.append(ids)
        return ids


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


def test_methodology_category_is_collected(tmp_path, monkeypatch):
    import app.rag.ingest as ingest

    methodology_dir = tmp_path / "methodology"
    methodology_dir.mkdir()
    (methodology_dir / "methodology_var_cvar_2026.pdf").write_bytes(b"fake")
    monkeypatch.setattr(ingest, "load_pdf_text", lambda _path: "완성된 방법론 본문")

    loaded = ingest.collect_corpus_texts(corpus_dir=str(tmp_path))

    assert "methodology" in CATEGORIES
    assert loaded == [
        ("methodology_var_cvar_2026.pdf", "완성된 방법론 본문", "methodology")
    ]


def test_add_chunks_batches_are_limited_to_embed_batch_size():
    embedder = _FakeEmbedder()
    store = _FakeStore(embedder)
    chunks = _make_chunks(1363)

    add_chunks_with_retries(store, chunks)

    assert sum(embedder.batch_sizes) == 1363
    assert max(embedder.batch_sizes) <= EMBED_BATCH_SIZE
    assert len(embedder.batch_sizes) == 22
    assert store.ids_by_call[0][0] == "bulk.pdf::0000"
    assert store.ids_by_call[-1][-1] == "bulk.pdf::1362"


def test_add_chunks_retries_rate_limit_then_succeeds(monkeypatch):
    sleeps: list[int] = []
    import app.rag.ingest as ingest

    monkeypatch.setattr(ingest.time, "sleep", lambda seconds: sleeps.append(seconds))
    embedder = _FakeEmbedder(failures=2)
    store = _FakeStore(embedder)

    add_chunks_with_retries(store, _make_chunks(3))

    assert embedder.calls == 3
    assert sleeps == [RATE_LIMIT_WAIT_SECONDS, RATE_LIMIT_WAIT_SECONDS]
    assert store.ids_by_call == [["bulk.pdf::0000", "bulk.pdf::0001", "bulk.pdf::0002"]]


def test_add_chunks_raises_after_max_rate_limit_retries(monkeypatch):
    sleeps: list[int] = []
    import app.rag.ingest as ingest

    monkeypatch.setattr(ingest.time, "sleep", lambda seconds: sleeps.append(seconds))
    embedder = _FakeEmbedder(failures=MAX_RATE_LIMIT_RETRIES + 1)
    store = _FakeStore(embedder)

    with pytest.raises(RuntimeError, match="재시도 한도"):
        add_chunks_with_retries(store, _make_chunks(1))

    assert embedder.calls == MAX_RATE_LIMIT_RETRIES + 1
    assert sleeps == [RATE_LIMIT_WAIT_SECONDS] * MAX_RATE_LIMIT_RETRIES
