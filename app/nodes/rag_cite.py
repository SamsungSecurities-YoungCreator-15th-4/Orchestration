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

from app.rag.citations import Citation, verify_citations
from app.state import RiskState

log = logging.getLogger(__name__)

MAX_CANDIDATES = 8  # LLM 인용 후보 상한 (프롬프트 지시용)


# ---------------------------------------------------------------------------
# 순수 헬퍼 (결정론)
# ---------------------------------------------------------------------------
def _build_query(metrics: dict) -> str:
    """metrics에서 결정론적으로 검색 질의를 만든다."""
    parts = ["VaR CVaR 리스크 지표 해석", "스트레스 테스트 시나리오"]
    var_block = metrics.get("var") or {}
    if var_block:
        parts.append("신뢰수준 " + " ".join(sorted(str(k) for k in var_block)))
    return " / ".join(parts)


def _build_explanations(metrics: dict, revision: int, judge_feedback: str) -> list[dict]:
    """결정론적 설명 문단 생성 (metrics 기반, LLM 미사용)."""
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
                "고금리·강달러 복합 충격 시나리오는 금리 민감 자산과 "
                "국내주식의 동반 하락을 가정한 것으로, 역사적 분포 기반 "
                "VaR가 포착하지 못하는 꼬리 위험을 보완한다."
            ),
            "revision": revision,
        },
    ]
    if judge_feedback:
        explanations.append(
            {"topic": "재작성 반영", "text": f"judge 피드백 반영: {judge_feedback}", "revision": revision}
        )
    return explanations


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

    source_by_id = {c["chunk_id"]: c.get("source", "") for c in chunks}
    out: list[Citation] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        quote = str(it.get("quote", "")).strip()
        chunk_id = str(it.get("chunk_id", "")).strip()
        if not quote or not chunk_id:
            continue
        out.append(
            Citation(
                quote=quote,
                source=str(it.get("source", "")) or source_by_id.get(chunk_id, ""),
                chunk_id=chunk_id,
                claim=str(it.get("claim", "")),
            )
        )
    return out


def _build_prompt(metrics: dict, chunks: list[dict], judge_feedback: str) -> str:
    chunk_lines = [
        f"[chunk_id={c['chunk_id']} source={c['source']}]\n{c['text']}" for c in chunks
    ]
    feedback_line = f"\n직전 judge 피드백(반영할 것): {judge_feedback}\n" if judge_feedback else ""
    return (
        "너는 리스크 리포트의 인용 담당자다. 아래 리스크 지표의 수치·주장을 "
        "뒷받침하는 인용을 근거 청크에서 고른다.\n"
        "규칙: quote는 반드시 해당 청크 원문에서 글자 그대로(공백 차이만 허용) "
        "복사한다. 원문에 없는 문장을 지어내면 검증에서 탈락한다.\n"
        f"최대 {MAX_CANDIDATES}개. 다음 JSON 배열만 출력하라: "
        '[{"claim": "...", "quote": "...", "chunk_id": "...", "source": "..."}]\n'
        f"{feedback_line}\n"
        f"## 리스크 지표\n{json.dumps(metrics, ensure_ascii=False, default=str)}\n\n"
        "## 근거 청크\n" + "\n\n".join(chunk_lines)
    )


def _llm_text(response) -> str:
    """LangChain 메시지/문자열 어느 쪽이 와도 텍스트를 얻는다."""
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)


# ---------------------------------------------------------------------------
# 노드 본체
# ---------------------------------------------------------------------------
def rag_cite(state: RiskState, *, llm=None, retriever=None) -> dict:
    """그래프 노드. llm/retriever 미주입 시 지연 구성, 불가하면 폴백."""
    metrics = state.get("metrics") or {}
    revision = state.get("judge_retries", 0)  # judge 루프 재작성 횟수
    judge_feedback = state.get("judge_feedback") or ""

    explanations = _build_explanations(metrics, revision, judge_feedback)

    # --- 1) retriever 준비 (미주입 시 지연 구성; 실패하면 폴백) ---
    if retriever is None:
        try:
            from app.rag.retriever import build_retriever

            retriever = build_retriever()
        except Exception as e:  # 인덱스 없음 / Azure 환경변수 없음 등
            log.warning("RAG 검색 불가 — 폴백(빈 인용): %s", e)
            return {"explanations": explanations, "citations": []}

    # --- 2) 근거 청크 검색 (임베딩 API·Chroma 쿼리 — 네트워크 오류 가능) ---
    from app.rag.retriever import retrieve_chunks

    try:
        chunks = retrieve_chunks(retriever, _build_query(metrics))
    except Exception as e:
        log.warning("RAG 검색 중 오류 — 폴백(빈 인용): %s", e)
        return {"explanations": explanations, "citations": []}
    if not chunks:
        log.warning("검색 결과 청크 없음 — 폴백(빈 인용)")
        return {"explanations": explanations, "citations": []}

    # --- 3) LLM 인용 후보 (미주입 시 팩토리; 실패하면 폴백) ---
    if llm is None:
        try:
            from app.llm.client import get_llm

            llm = get_llm(temperature=0.0)
        except Exception as e:
            log.warning("LLM 구성 불가 — 폴백(빈 인용): %s", e)
            return {"explanations": explanations, "citations": []}

    try:
        raw = _llm_text(llm.invoke(_build_prompt(metrics, chunks, judge_feedback)))
    except Exception as e:
        log.warning("LLM 호출 실패 — 폴백(빈 인용): %s", e)
        return {"explanations": explanations, "citations": []}

    candidates = parse_candidates(raw, chunks)

    # --- 4) 결정론 검증 — 통과분만 state에 기록 ---
    verified, rejected = verify_citations(candidates, chunks)
    for r in rejected:
        log.warning(
            "인용 탈락: chunk_id=%s source=%s — %s (quote=%.60s…)",
            r["chunk_id"], r["source"], r["reason"], r["quote"],
        )

    return {
        "explanations": explanations,
        "citations": [c.to_dict() for c in verified],  # 검증 통과분만
    }
