"""설명·인용 생성 노드 — RAG 검색 + LLM 인용 후보 + 결정론 검증.

흐름:
  1) state.metrics(var_engine 산출)를 읽어 검색 질의를 만든다.
  2) retriever(Chroma persist)로 근거 청크를 검색한다.
  3) LLM(AzureChatOpenAI, LangChain 경유)이 수치별 인용 후보를 JSON으로 구성한다.
  4) app.rag.citations.verify_citations(순수 결정론)로 검증한다.
  5) ★검증을 통과한 인용만★ state의 citations 키에 기록한다. 탈락분은 사유 로그.

judge 재작성 루프로 재방문될 수 있으므로, 반환값은 항상 explanations/citations를
통째로 새로 만들어 이전 결과를 덮어쓴다(누적 없음 → 재실행 안전).

인덱스나 Azure 키가 없는 로컬 스켈레톤 실행에서는 RAG 경로를 건너뛰고
결정론적 설명 + 빈 인용으로 폴백해, 그래프 완주(run_graph.py)를 깨지 않는다.

LLM/retriever는 의존성 주입이 가능해 테스트에서 fake를 넣을 수 있다.
"""
from __future__ import annotations

import json
import logging
import re

from app.llm.audit import with_llm_audit
from app.observability.langsmith import annotate_current_run
from app.rag.citations import Citation, verify_citations
from app.rag.ingest import CHUNK_SIZE
from app.state import RiskState

log = logging.getLogger(__name__)

MAX_CANDIDATES = 8  # LLM 인용 후보 상한 (프롬프트 지시용)
MAX_EVIDENCE_QUOTE_CHARS = 220  # UI 한 행에서 읽을 수 있는 근거 문장 상한
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+")
_SENTENCE_END_RE = re.compile(r"[.!?。][\"'”’)]*$")
_UNREADABLE_HANGUL_RUN_RE = re.compile(r"[가-힣]{16,}")
_SECTION_HEADING_LINE_RE = re.compile(r"^\d+(?:\.\d+)*\.\s+[^.!?。]{1,80}$")


# ---------------------------------------------------------------------------
# 순수 헬퍼 (결정론)
# ---------------------------------------------------------------------------
def _build_query(topic: str, metrics: dict) -> str:
    """설명 topic과 metrics에서 결정론적인 전용 검색 질의를 만든다."""
    if topic == "VaR 해석":
        parts = [
            "Historical Simulation VaR CVaR 해석",
            "신뢰수준 보유기간 관측기간 초과손실",
        ]
        confidence = metrics.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            parts.append(f"신뢰수준 {confidence * 100:g}%")
        horizons = metrics.get("horizons") or {}
        if isinstance(horizons, dict) and horizons:
            parts.append("보유기간 " + " ".join(sorted(str(key) for key in horizons)))
        n_observations = (metrics.get("meta") or {}).get("n_observations")
        if isinstance(n_observations, int) and not isinstance(n_observations, bool):
            parts.append(f"관측기간 {n_observations}거래일")
        return " / ".join(parts)

    if topic == "스트레스 시나리오":
        parts = [
            "Historical Simulation 과거 관측되지 않은 위기 스트레스 테스트 보완",
            "고금리 강달러 코로나 개별 시나리오 최대 손실 복합 위기 아님",
        ]
        stress = metrics.get("stress") or {}
        if isinstance(stress, dict) and stress:
            parts.append("시나리오 " + " ".join(sorted(str(key) for key in stress)))
        return " / ".join(parts)

    if topic == "기준일 및 유의사항":
        return (
            "VaR CVaR 방법론 표기 규약 신뢰수준 보유기간 기준일 / "
            "과거 데이터 통계적 추정치 실제 결과 차이 과도한 정밀 확률 표현"
        )

    return f"리스크 방법론 근거 재검증 / {topic}"


def _build_explanations(
    metrics: dict,
    revision: int,
    judge_feedback: str,
    as_of_date: str | None,
) -> list[dict]:
    """결정론적 설명 문단 생성 (metrics 기반, LLM 미사용)."""
    reference_date = as_of_date or "미지정"
    explanations = [
        {
            "topic": "VaR 해석",
            "text": (
                "99% 1일 VaR는 정상 시장에서 하루 동안 발생할 수 있는 "
                "최대 손실의 통계적 추정치이며, 100일 중 1일 정도는 "
                "이를 초과하는 손실이 발생할 수 있음을 의미한다."
            ),
            "revision": revision,
        },
        {
            "topic": "스트레스 시나리오",
            "text": (
                "스트레스 테스트는 Historical VaR의 구조적 한계 — 과거 관측 기간의 "
                "표본 분포에 담기지 않은 충격을 반영하지 못함 — 을 보완하기 위한 절차다."
            ),
            "revision": revision,
        },
        {
            "topic": "기준일 및 유의사항",
            "text": (
                f"기준일은 {reference_date}입니다. 본 설명은 과거 데이터 기반 "
                "리스크 추정치이며 투자 권유가 아니고, 원금 또는 수익을 "
                "보장하지 않습니다. 실제 결과와 다를 수 있습니다."
            ),
            "revision": revision,
        },
    ]
    if judge_feedback:
        explanations.append(
            {"topic": "재작성 반영", "text": f"judge 피드백 반영: {judge_feedback}", "revision": revision}
        )
    return explanations


def _evidence_rows(chunks: list[dict]) -> list[dict]:
    """PDF 줄바꿈을 합친 문장 단위 근거를 결정론적으로 만든다.

    고정 길이 청크의 시작·끝은 문장 중간일 수 있다. 첫 청크가 아닌 경우의
    선행 조각과 가득 찬 청크의 후행 조각은 제외해 UI에 불완전한 인용문이
    노출되는 것을 막는다. 공백만 정규화하므로 최종 substring 검증 규약은
    그대로 유지된다.
    """
    rows: list[dict] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_id = chunk.get("chunk_id")
        text = chunk.get("text")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            continue
        if not isinstance(text, str):
            continue
        source = chunk.get("source")
        source = source if isinstance(source, str) else ""
        content_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not _SECTION_HEADING_LINE_RE.fullmatch(line.strip())
        ]
        normalized = " ".join(content_lines)
        if not normalized:
            continue

        sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(normalized) if part.strip()]
        char_start = chunk.get("char_start")
        char_end = chunk.get("char_end")
        starts_mid_document = isinstance(char_start, int) and char_start > 0
        full_sized_chunk = (
            isinstance(char_start, int)
            and isinstance(char_end, int)
            and char_end - char_start >= CHUNK_SIZE
        )

        if starts_mid_document:
            sentences = sentences[1:]
        if full_sized_chunk and sentences and not _SENTENCE_END_RE.search(sentences[-1]):
            sentences = sentences[:-1]

        readable_sentences = [
            quote
            for quote in sentences
            if len(quote) <= MAX_EVIDENCE_QUOTE_CHARS
            and not _UNREADABLE_HANGUL_RUN_RE.search(quote)
        ]
        for sentence_no, quote in enumerate(readable_sentences, 1):
            rows.append(
                {
                    "evidence_id": f"{chunk_id}#S{sentence_no:03d}",
                    "quote": quote,
                    "chunk_id": chunk_id,
                    "source": source,
                }
            )
    return rows


def parse_candidates(raw: str, chunks: list[dict]) -> list[Citation]:
    """LLM 응답 텍스트에서 인용 후보 JSON 배열을 파싱해 Citation 목록으로.

    형식 오류·필드 누락 항목은 조용히 버린다(검증 이전 단계의 방어).
    chunk_id가 검색 청크에 없는 후보도 여기서는 남겨둔다 —
    최종 판정은 verify_citations(순수 결정론)가 한다.
    """
    # [{ … }] 형태만 타겟팅 — LLM이 [참고] 같은 대괄호 문구를 덧붙여도 안전
    m = re.search(r"\[\s*\{.*\}\s*\]", raw, flags=re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []

    evidence_rows = _evidence_rows(chunks)
    source_by_id = {row["chunk_id"]: row["source"] for row in evidence_rows}
    evidence_by_id = {row["evidence_id"]: row for row in evidence_rows}
    out: list[Citation] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        evidence = evidence_by_id.get(str(it.get("evidence_id", "")).strip())
        quote = evidence["quote"] if evidence else str(it.get("quote", "")).strip()
        chunk_id = evidence["chunk_id"] if evidence else str(it.get("chunk_id", "")).strip()
        if not quote or not chunk_id:
            continue
        out.append(
            Citation(
                quote=quote,
                source=(
                    evidence["source"]
                    if evidence
                    else str(it.get("source", "")) or source_by_id.get(chunk_id, "")
                ),
                chunk_id=chunk_id,
                claim=str(it.get("claim", "")),
            )
        )
    return out


def _build_prompt(
    topic: str,
    explanation: dict,
    metrics: dict,
    chunks: list[dict],
    judge_feedback: str,
) -> str:
    evidence_lines = [
        (
            f"[evidence_id={row['evidence_id']} chunk_id={row['chunk_id']} "
            f"source={row['source']}]\n{row['quote']}"
        )
        for row in _evidence_rows(chunks)
    ]
    feedback_line = f"\n직전 judge 피드백(반영할 것): {judge_feedback}\n" if judge_feedback else ""
    return (
        "너는 리스크 리포트의 인용 담당자다. 아래 단일 topic 설명의 수치·주장을 "
        "뒷받침하는 인용을 이 topic 전용 근거 청크에서만 고른다.\n"
        "규칙: 설명문의 각 실질적 주장을 뒷받침하는 evidence_id를 아래 근거 문장에서 "
        "고른다. evidence_id를 만들거나 여러 근거 문장을 합치지 않는다.\n"
        f"모든 claim은 정확히 {json.dumps(topic, ensure_ascii=False)}로 출력하라. "
        "근거가 없으면 무관한 인용을 만들지 말고 빈 배열 []을 출력하라.\n"
        f"최대 {MAX_CANDIDATES}개. 다음 JSON 배열만 출력하라: "
        '[{"claim": "...", "evidence_id": "..."}]\n'
        f"{feedback_line}\n"
        f"## 설명 topic\n{topic}\n\n"
        f"## 설명문\n{explanation.get('text', '')}\n\n"
        f"## 리스크 지표\n{json.dumps(metrics, ensure_ascii=False, default=str)}\n\n"
        "## 근거 문장\n" + "\n\n".join(evidence_lines)
    )


def _llm_text(response) -> str:
    """LangChain 메시지/문자열 어느 쪽이 와도 텍스트를 얻는다."""
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)


def _result_with_audit(
    *,
    run_config: dict,
    explanations: list[dict],
    citations: list[dict],
    revision: int,
    prompts: dict[str, str],
    llm=None,
    responses: list[object] | None = None,
) -> dict:
    audited_config = with_llm_audit(
        run_config,
        component="rag_cite",
        attempt=revision + 1,
        prompts=prompts,
        llm=llm,
        responses=responses or [],
    )
    latest = audited_config["audit"]["llm"]["rag_cite"]["latest"]
    annotate_current_run(
        metadata={
            "rag_attempt": revision + 1,
            "rag_prompt_hash": latest["prompt_hash"]["aggregate_sha256"],
            "rag_verified_citations": len(citations),
            "model_version": latest["model_version"],
        },
        tags=[f"rag-attempt:{revision + 1}"],
    )
    return {
        "run_config": audited_config,
        "explanations": explanations,
        "citations": citations,
    }


# ---------------------------------------------------------------------------
# 노드 본체
# ---------------------------------------------------------------------------
def rag_cite(state: RiskState, *, llm=None, retriever=None) -> dict:
    """그래프 노드. llm/retriever 미주입 시 지연 구성, 불가하면 폴백."""
    metrics = state.get("metrics") or {}
    run_config = state.get("run_config") or {}
    revision = state.get("judge_retries", 0)  # judge 루프 재작성 횟수
    judge_feedback = state.get("judge_feedback") or ""
    meta = metrics.get("meta") or {}
    data_period = meta.get("data_period") or {}
    as_of_date = data_period.get("end") or run_config.get("as_of_date")

    explanations = _build_explanations(metrics, revision, judge_feedback, as_of_date)
    prompts: dict[str, str] = {}
    responses: list[object] = []

    # --- 1) retriever 준비 (미주입 시 지연 구성; 실패하면 폴백) ---
    if retriever is None:
        try:
            from app.rag.retriever import build_retriever

            retriever = build_retriever()
        except Exception as e:  # 인덱스 없음 / Azure 환경변수 없음 등
            log.warning("RAG 검색 불가 — 폴백(빈 인용): %s", e)
            return _result_with_audit(
                run_config=run_config,
                explanations=explanations,
                citations=[],
                revision=revision,
                prompts=prompts,
            )

    # --- 2) LLM 준비 ---
    if llm is None:
        try:
            from app.llm.client import get_llm

            llm = get_llm(temperature=0.0)
        except Exception as e:
            log.warning("LLM 구성 불가 — 폴백(빈 인용): %s", e)
            return _result_with_audit(
                run_config=run_config,
                explanations=explanations,
                citations=[],
                revision=revision,
                prompts=prompts,
            )

    # --- 3) topic별 검색 → 후보 생성 → 해당 검색 근거 안에서 검증 ---
    from app.rag.retriever import retrieve_chunks

    all_verified: list[Citation] = []
    for explanation in explanations:
        topic = str(explanation.get("topic", "")).strip()
        query = _build_query(topic, metrics)
        try:
            chunks = retrieve_chunks(retriever, query)
        except Exception as e:
            log.warning("topic=%s RAG 검색 중 오류 — 해당 topic 건너뜀: %s", topic, e)
            continue
        if not chunks:
            log.warning("topic=%s 검색 결과 청크 없음 — 해당 topic 건너뜀", topic)
            continue

        try:
            prompt = _build_prompt(topic, explanation, metrics, chunks, judge_feedback)
            prompts[topic] = prompt
            response = llm.invoke(prompt)
            responses.append(response)
            raw = _llm_text(response)
        except Exception as e:
            log.warning("topic=%s LLM 호출 실패 — 해당 topic 건너뜀: %s", topic, e)
            continue

        candidates = parse_candidates(raw, chunks)
        for candidate in candidates:
            llm_claim = candidate.claim
            candidate.claim = topic
            if llm_claim and llm_claim != topic:
                candidate.extra = {**candidate.extra, "llm_claim": llm_claim}

        verified, rejected = verify_citations(candidates, chunks)
        chunk_by_id = {
            chunk.get("chunk_id"): chunk
            for chunk in chunks
            if chunk.get("chunk_id")
        }
        for citation in verified:
            chunk = chunk_by_id.get(citation.chunk_id) or {}
            citation.extra = {
                **citation.extra,
                "chunk_text": chunk.get("text", ""),
                "category": chunk.get("category", ""),
            }
        all_verified.extend(verified)
        for rejected_citation in rejected:
            log.warning(
                "topic=%s 인용 탈락: chunk_id=%s source=%s — %s (quote=%.60s…)",
                topic,
                rejected_citation["chunk_id"],
                rejected_citation["source"],
                rejected_citation["reason"],
                rejected_citation["quote"],
            )

    unique_verified: list[Citation] = []
    seen = set()
    for citation in all_verified:
        key = (citation.claim, citation.chunk_id, citation.quote)
        if key not in seen:
            seen.add(key)
            unique_verified.append(citation)

    return _result_with_audit(
        run_config=run_config,
        explanations=explanations,
        citations=[citation.to_dict() for citation in unique_verified],
        revision=revision,
        prompts=prompts,
        llm=llm,
        responses=responses,
    )
