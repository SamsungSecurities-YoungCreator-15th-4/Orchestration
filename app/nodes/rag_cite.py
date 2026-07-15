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
from app.rag.contracts import EVIDENCE_ROLES, ROUTING_CONTRACT
from app.rag.ingest import CHUNK_SIZE
from app.state import RiskState

log = logging.getLogger(__name__)

MAX_CANDIDATES = 8  # LLM 인용 후보 상한 (프롬프트 지시용)
MAX_EVIDENCE_QUOTE_CHARS = 320  # 외부 리서치 PDF의 긴 문장도 한 문장 범위에서 허용
MAX_ROUTE_CHUNKS = 6
MAX_CHUNKS_PER_SOURCE = 2
TOPIC_CATEGORIES = {
    "VaR 해석": "methodology",
    "스트레스 시나리오": "methodology",
    "기준일 및 유의사항": "methodology",
    "거시환경·스트레스 개연성": "macro",
    "자산시장 참고": "house_view",
    "세무 참고": "tax",
    "재작성 반영": "methodology",
}
ASSET_LABELS = {
    "domestic_equity": "국내주식",
    "global_equity": "해외주식",
    "domestic_bond": "국내채권",
    "global_bond": "해외채권",
    "alternatives": "대체투자",
    "cash": "현금성자산",
}
TAX_KEYWORDS = (
    "금융소득",
    "종합과세",
    "종합소득",
    "양도소득",
    "배당소득",
    "이자소득",
    "상속",
    "증여",
    "절세",
    "과세",
    "세무",
    "세금",
    "법인",
)
NO_TAX_ISSUE_VALUES = frozenset(
    {
        "",
        "-",
        "없음",
        "없습니다",
        "해당없음",
        "해당사항없음",
        "특이사항없음",
        "확인필요",
        "미정",
        "모름",
    }
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[!?。])\s*|(?<=\.)\s*(?!\d)")
_SENTENCE_END_RE = re.compile(r"[.!?。][\"'”’)]*$")
_SECTION_HEADING_LINE_RE = re.compile(r"^\d+(?:\.\d+)*\.\s+[^.!?。]{1,80}$")
_TOC_LEADER_RE = re.compile(r"[.·…]{6,}")
_PAGE_COUNTER_RE = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
_NUMERIC_TABLE_LINE_RE = re.compile(r"^[\s\d.,%()'’\-+~∼]+$")
_BULLET_LINE_RE = re.compile(r"^[•□▪⦁o\-]\s*")
_BARE_BULLET_LINE_RE = re.compile(r"^[•□▪⦁o\-]\s*$")
_NOISE_PREFIXES = ("자료:", "출처:", "참고:", "주:")
_NOISE_MARKERS = (
    "Samsung Securities",
    "www.samsungpop.com",
    "http://",
    "https://",
    "Tel:",
    "Fax:",
    "E-mail:",
    "공보관:",
    "이자료는 배포시부터",
)
_BOILERPLATE_MARKERS = ("본조사분석자료에수록된내용",)
_SHORT_LATIN_FRAGMENT_RE = re.compile(r"^[A-Za-z]{1,4}[.)]?")


# ---------------------------------------------------------------------------
# 순수 헬퍼 (결정론)
# ---------------------------------------------------------------------------
def _top_cvar_asset(metrics: dict) -> str | None:
    """CVaR 원화 기여도가 가장 큰 자산군을 결정론적으로 고른다."""
    raw = (metrics.get("drilldown") or {}).get("tail_contribution_krw") or {}
    if not isinstance(raw, dict):
        return None
    candidates = [
        (str(asset_class), float(value))
        for asset_class, value in raw.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (-item[1], item[0]))[0]


def _tax_issue_terms(ips: dict) -> tuple[str, ...]:
    """개인 원문을 질의에 복사하지 않고 실질 세무 키워드만 추린다."""
    raw_tax = ips.get("Tax") if isinstance(ips, dict) else None
    if not isinstance(raw_tax, str):
        return ()
    normalized = re.sub(r"\s+", "", raw_tax).strip().lower()
    if normalized in NO_TAX_ISSUE_VALUES or any(
        marker in normalized
        for marker in ("해당사항없", "특이사항없", "세무이슈없", "세금이슈없")
    ):
        return ()
    terms: list[str] = []
    for keyword in TAX_KEYWORDS:
        if keyword.lower() not in normalized:
            continue
        if any(keyword in selected for selected in terms):
            continue
        terms.append(keyword)
    return tuple(terms)


def _category_for_topic(topic: str) -> str:
    """설명 topic을 합의된 단일 corpus category로 라우팅한다."""
    return TOPIC_CATEGORIES.get(topic, "methodology")


def _routing_reason(topic: str, metrics: dict, ips: dict) -> str:
    """민감 원문 없이 topic이 해당 문서군으로 간 결정론적 사유를 만든다."""
    category = _category_for_topic(topic)
    if category == "macro":
        return "과제 전제인 고금리·강달러 거시환경의 개연성 검증"
    if category == "house_view":
        asset_class = _top_cvar_asset(metrics)
        asset_label = ASSET_LABELS.get(asset_class or "", asset_class or "미확인")
        return f"CVaR 기여도 1위 자산군: {asset_label}({asset_class or 'unknown'})"
    if category == "tax":
        terms = _tax_issue_terms(ips)
        return "IPS 세무 키워드: " + ", ".join(terms)
    return f"정량 결과의 계산·해석 방법론 근거: {topic}"


def _routing_records(
    explanations: list[dict], metrics: dict, ips: dict
) -> list[dict]:
    """이번 실행에서 활성화된 검색 route를 감사 가능한 형태로 고정한다."""
    records: list[dict] = []
    for explanation in explanations:
        topic = str(explanation.get("topic", "")).strip()
        category = _category_for_topic(topic)
        records.append(
            {
                "topic": topic,
                "category": category,
                "evidence_role": EVIDENCE_ROLES[category],
                "routing_reason": _routing_reason(topic, metrics, ips),
            }
        )
    return records


def _build_query(topic: str, metrics: dict, ips: dict | None = None) -> str:
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

    if topic == "거시환경·스트레스 개연성":
        return (
            "한국은행 통화정책방향 기준금리 원달러 환율 변동성 금융시장 하방위험 / "
            "Federal Reserve FOMC policy rate US dollar financial conditions / "
            "고금리 강달러 스트레스 시나리오 거시환경 개연성"
        )

    if topic == "자산시장 참고":
        asset_class = _top_cvar_asset(metrics)
        asset_label = ASSET_LABELS.get(asset_class or "", asset_class or "자산군")
        asset_terms = {
            "domestic_equity": "KOSPI 한국 주식 업종 밸류에이션",
            "global_equity": "글로벌 주식 미국 증시 업종 밸류에이션",
            "domestic_bond": "국내 채권 국고채 금리 듀레이션",
            "global_bond": "글로벌 채권 미국 국채 금리 듀레이션",
            "alternatives": "대체투자 원자재 리츠 변동성",
            "cash": "현금성자산 단기금리 유동성",
        }.get(asset_class or "", "")
        return (
            f"{asset_label} {asset_terms} 시장 전망 변동성 하방 위험 / "
            "CVaR 기여도 상위 자산군 해석 참고"
        )

    if topic == "세무 참고":
        tax_terms = _tax_issue_terms(ips or {})
        supplements: list[str] = []
        finance_terms = {"금융소득", "종합과세", "배당소득", "이자소득"}
        if finance_terms.intersection(tax_terms):
            supplements.extend(["비과세", "분리과세", "종합소득세", "확정신고"])
        if "양도소득" in tax_terms:
            supplements.extend(["양도소득세", "과세", "신고"])
        if "법인" in tax_terms:
            supplements.extend(["법인세", "신고"])
        focused_terms = list(dict.fromkeys([*tax_terms, *supplements]))
        return f"국세청 {' '.join(focused_terms)} 과세 신고 기준"

    return f"리스크 방법론 근거 재검증 / {topic}"


def _build_explanations(
    metrics: dict,
    revision: int,
    judge_feedback: str,
    as_of_date: str | None,
    ips: dict | None = None,
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
    explanations.append(
        {
            "topic": "거시환경·스트레스 개연성",
            "text": (
                "고금리·강달러 충격은 금리·환율·위험자산 가격 경로를 함께 "
                "점검해야 하는 거시 시나리오이며, 정량 결과의 해석 참고자료로 사용한다."
            ),
            "revision": revision,
        }
    )

    top_asset = _top_cvar_asset(metrics)
    if top_asset:
        asset_label = ASSET_LABELS.get(top_asset, top_asset)
        explanations.append(
            {
                "topic": "자산시장 참고",
                "text": (
                    f"CVaR 기여도가 가장 큰 {asset_label} 관련 시장 전망은 "
                    "포트폴리오 집중위험을 해석하는 참고자료로만 사용한다."
                ),
                "revision": revision,
            }
        )

    tax_terms = _tax_issue_terms(ips or {})
    if tax_terms:
        explanations.append(
            {
                "topic": "세무 참고",
                "text": (
                    f"IPS에서 확인된 세무 이슈({', '.join(tax_terms)})는 세후 "
                    "유동성과 투자구조를 해석하는 참고자료로만 사용한다."
                ),
                "revision": revision,
            }
        )
    if judge_feedback:
        explanations.append(
            {
                "topic": "재작성 반영",
                "text": f"judge 피드백 반영: {judge_feedback}",
                "revision": revision,
            }
        )
    return explanations


def _is_noise_line(line: str) -> bool:
    """목차·페이지번호·표 숫자·반복 머리말처럼 인용 가치가 없는 줄인지 판정한다."""
    stripped = line.strip()
    if not stripped:
        return True
    if _TOC_LEADER_RE.search(stripped) or _PAGE_COUNTER_RE.fullmatch(stripped):
        return True
    if _NUMERIC_TABLE_LINE_RE.fullmatch(stripped):
        return True
    if _SHORT_LATIN_FRAGMENT_RE.fullmatch(stripped):
        return True
    if stripped.startswith(_NOISE_PREFIXES):
        return True
    return any(marker in stripped for marker in _NOISE_MARKERS)


def _is_readable_quote(quote: str) -> bool:
    """짧은 숫자 조각과 깨진 무공백 문장을 제외하되 정상 장문은 허용한다."""
    if not quote or len(quote) > MAX_EVIDENCE_QUOTE_CHARS:
        return False
    compact = re.sub(r"\s+", "", quote)
    if _is_noise_line(quote) or any(
        marker in compact for marker in _BOILERPLATE_MARKERS
    ):
        return False
    meaningful = re.findall(r"[A-Za-z가-힣]", quote)
    return len(meaningful) >= 2


def _fallback_line_quotes(
    content_lines: list[str],
    *,
    starts_mid_document: bool,
    full_sized_chunk: bool,
) -> list[str]:
    """문장부호가 부족한 PDF 줄을 경계 안에서 이어 읽을 수 있는 근거로 복원한다."""
    lines = list(content_lines)
    if starts_mid_document and lines:
        lines = lines[1:]
    if full_sized_chunk and lines:
        lines = lines[:-1]

    quotes: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        quote = re.sub(r"\s+", " ", buffer).strip()
        if _is_readable_quote(quote):
            quotes.append(quote)
        buffer = ""

    for line in lines:
        if (
            _is_noise_line(line)
            or _SECTION_HEADING_LINE_RE.fullmatch(line)
            or _BARE_BULLET_LINE_RE.fullmatch(line)
        ):
            flush()
            continue
        if buffer and _BULLET_LINE_RE.match(line):
            flush()
        candidate = f"{buffer} {line}".strip() if buffer else line
        if len(candidate) > MAX_EVIDENCE_QUOTE_CHARS:
            flush()
            if len(line) <= MAX_EVIDENCE_QUOTE_CHARS:
                buffer = line
        else:
            buffer = candidate
        if buffer and _SENTENCE_END_RE.search(buffer):
            flush()
    flush()
    return quotes


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
        normalized_original = re.sub(r"\s+", " ", text).strip()
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

        sentences = [
            part.strip()
            for part in _SENTENCE_SPLIT_RE.split(normalized)
            if part.strip()
        ]
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
        if (
            full_sized_chunk
            and sentences
            and not _SENTENCE_END_RE.search(sentences[-1])
        ):
            sentences = sentences[:-1]

        fallback_quotes = _fallback_line_quotes(
            content_lines,
            starts_mid_document=starts_mid_document,
            full_sized_chunk=full_sized_chunk,
        )
        readable_sentences: list[str] = []
        seen_quotes: set[str] = set()
        for quote in [*sentences, *fallback_quotes]:
            normalized_quote = re.sub(r"\s+", " ", quote).strip()
            if (
                _is_readable_quote(normalized_quote)
                and normalized_quote in normalized_original
                and normalized_quote not in seen_quotes
            ):
                seen_quotes.add(normalized_quote)
                readable_sentences.append(normalized_quote)
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


def _select_diverse_chunks(chunks: list[dict]) -> list[dict]:
    """검색 순위를 보존하며 동일 문서 독점을 막아 category 내부 출처를 분산한다."""
    selected: list[dict] = []
    source_counts: dict[str, int] = {}
    seen_chunk_ids: set[str] = set()
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        if not chunk_id or chunk_id in seen_chunk_ids:
            continue
        source = str(chunk.get("source") or chunk_id)
        if source_counts.get(source, 0) >= MAX_CHUNKS_PER_SOURCE:
            continue
        selected.append(chunk)
        seen_chunk_ids.add(chunk_id)
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(selected) >= MAX_ROUTE_CHUNKS:
            break
    return selected


def _valid_chunks(
    chunks: list[dict],
    *,
    expected_category: str | None = None,
) -> list[dict]:
    """검색 계약과 route category를 만족하는 청크만 남긴다."""
    valid: list[dict] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_id = chunk.get("chunk_id")
        text = chunk.get("text")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            continue
        if not isinstance(text, str):
            continue
        normalized = dict(chunk)
        normalized["source"] = (
            chunk.get("source") if isinstance(chunk.get("source"), str) else ""
        )
        normalized["category"] = (
            chunk.get("category") if isinstance(chunk.get("category"), str) else ""
        )
        normalized["published_at"] = (
            chunk.get("published_at")
            if isinstance(chunk.get("published_at"), str)
            else ""
        )
        if expected_category and normalized["category"] != expected_category:
            continue
        valid.append(normalized)
    return valid


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
        chunk_id = (
            evidence["chunk_id"] if evidence else str(it.get("chunk_id", "")).strip()
        )
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
    query: str,
) -> str:
    evidence_lines = [
        (
            f"[evidence_id={row['evidence_id']} chunk_id={row['chunk_id']} "
            f"source={row['source']}]\n{row['quote']}"
        )
        for row in _evidence_rows(chunks)
    ]
    feedback_line = (
        f"\n직전 judge 피드백(반영할 것): {judge_feedback}\n" if judge_feedback else ""
    )
    category = _category_for_topic(topic)
    if category == "methodology":
        selection_rule = (
            "이 topic은 계산 방법론 근거다. 설명문의 실질적 주장을 직접 "
            "뒷받침하는 문장만 선택한다."
        )
    elif category == "tax":
        selection_rule = (
            "이 topic은 정량 계산 입력이 아닌 세무 해석 참고다. 근거 선택 초점의 "
            "세무 키워드 가운데 하나 이상과 직접 관련된 과세·신고·감면 규정이나 "
            "사례를 선택한다. 모든 키워드를 한 문장이 동시에 뒷받침하거나 특정 "
            "포트폴리오·CVaR 명칭이 원문에 있을 필요는 없다. 단순히 tax category라는 "
            "이유로 무관한 문장을 고르지 않는다."
        )
    else:
        selection_rule = (
            "이 topic은 정량 계산 입력이 아닌 해석 참고다. 설명문의 '참고자료로 "
            "사용한다'는 내부 역할 문구나 특정 포트폴리오·CVaR 명칭이 원문에 "
            "그대로 있을 필요는 없다. 대신 아래 근거 선택 초점과 직접 관련된 "
            "구체적 사실·전망·규정만 선택하고, 단순히 같은 category라는 이유로 "
            "무관한 문장을 고르지 않는다."
        )
    return (
        "너는 리스크 리포트의 인용 담당자다. 아래 단일 topic 설명의 수치·주장을 "
        "뒷받침하는 인용을 이 topic 전용 근거 청크에서만 고른다.\n"
        f"규칙: {selection_rule} evidence_id는 아래 근거 문장에서만 고르고, "
        "새로 만들거나 여러 근거 문장을 합치지 않는다.\n"
        f"모든 claim은 정확히 {json.dumps(topic, ensure_ascii=False)}로 출력하라. "
        "근거가 없으면 무관한 인용을 만들지 말고 빈 배열 []을 출력하라.\n"
        f"최대 {MAX_CANDIDATES}개. 다음 JSON 배열만 출력하라: "
        '[{"claim": "...", "evidence_id": "..."}]\n'
        f"{feedback_line}\n"
        f"## 설명 topic\n{topic}\n\n"
        f"## 설명문\n{explanation.get('text', '')}\n\n"
        f"## 근거 선택 초점\n{query}\n\n"
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
    routes: list[dict],
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
    latest["routing_contract"] = ROUTING_CONTRACT
    latest["routes"] = [dict(route) for route in routes]
    for record in audited_config["audit"]["llm"]["rag_cite"]["history"]:
        if record.get("attempt") == revision + 1:
            record["routing_contract"] = ROUTING_CONTRACT
            record["routes"] = [dict(route) for route in routes]
    route_categories = sorted({route["category"] for route in routes})
    evidence_roles = sorted({route["evidence_role"] for route in routes})
    missing_published_at = 0
    for citation in citations:
        extra = citation.get("extra")
        if not isinstance(extra, dict) or not extra.get("published_at"):
            missing_published_at += 1
    annotate_current_run(
        metadata={
            "rag_attempt": revision + 1,
            "rag_prompt_hash": latest["prompt_hash"]["aggregate_sha256"],
            "rag_verified_citations": len(citations),
            "rag_route_categories": ",".join(route_categories),
            "rag_evidence_roles": ",".join(evidence_roles),
            "rag_missing_published_at": missing_published_at,
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
    ips = state.get("ips") or {}
    meta = metrics.get("meta") or {}
    data_period = meta.get("data_period") or {}
    as_of_date = data_period.get("end") or run_config.get("as_of_date")

    explanations = _build_explanations(
        metrics,
        revision,
        judge_feedback,
        as_of_date,
        ips,
    )
    routes = _routing_records(explanations, metrics, ips)
    route_by_topic = {route["topic"]: route for route in routes}
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
                routes=routes,
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
                routes=routes,
            )

    # --- 3) topic별 검색 → 후보 생성 → 해당 검색 근거 안에서 검증 ---
    from app.rag.retriever import retrieve_chunks

    all_verified: list[Citation] = []
    for explanation in explanations:
        topic = str(explanation.get("topic", "")).strip()
        route = route_by_topic[topic]
        category = route["category"]
        query = _build_query(topic, metrics, ips)
        try:
            retrieved_chunks = retrieve_chunks(retriever, query, category=category)
        except Exception as e:
            log.warning("topic=%s RAG 검색 중 오류 — 해당 topic 건너뜀: %s", topic, e)
            continue
        valid_chunks = _valid_chunks(retrieved_chunks, expected_category=category)
        if len(valid_chunks) != len(retrieved_chunks):
            log.warning(
                "topic=%s 검색 결과에서 계약 위반 청크 %d건 제외",
                topic,
                len(retrieved_chunks) - len(valid_chunks),
            )
        chunks = _select_diverse_chunks(valid_chunks)
        if not chunks:
            log.warning("topic=%s 검색 결과 청크 없음 — 해당 topic 건너뜀", topic)
            continue

        try:
            prompt = _build_prompt(
                topic,
                explanation,
                metrics,
                chunks,
                judge_feedback,
                query,
            )
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
                candidate_extra = (
                    candidate.extra if isinstance(candidate.extra, dict) else {}
                )
                candidate.extra = {**candidate_extra, "llm_claim": llm_claim}

        verified, rejected = verify_citations(candidates, chunks)
        chunk_by_id = {
            chunk.get("chunk_id"): chunk
            for chunk in chunks
            if isinstance(chunk, dict) and chunk.get("chunk_id")
        }
        for citation in verified:
            chunk = chunk_by_id.get(citation.chunk_id) or {}
            citation_extra = citation.extra if isinstance(citation.extra, dict) else {}
            citation.extra = {
                **citation_extra,
                "chunk_text": chunk.get("text", ""),
                "category": chunk.get("category", ""),
                "evidence_role": route["evidence_role"],
                "routing_reason": route["routing_reason"],
                "published_at": chunk.get("published_at", ""),
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
        routes=routes,
        llm=llm,
        responses=responses,
    )
