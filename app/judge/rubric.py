"""설명문 품질을 판정하는 6축 루브릭.

결정론 축은 순수 파이썬으로만 동작한다. 환각·위조정밀도 축은 주입된
LangChain chat model의 ``invoke`` 인터페이스만 사용하며 SDK를 직접 import하지 않는다.
"""
from __future__ import annotations

import json
import math
import re

AXIS_NAMES = (
    "source_validity",
    "numeric_consistency",
    "hallucination",
    "false_precision",
    "disclaimer",
    "prohibited_expression",
)

PROHIBITED_TERMS = ("보장", "확정", "반드시", "무조건", "절대", "확실히")
NEGATION_MARKERS = ("않", "아니", "못", "없")
NEGATION_WINDOW = 15
DOUBLE_NEGATION_WINDOW = 40

_DATE_RE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<number>[+-]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>%|bp|억원|억|만원|원|거래일|일)"
)
_ENGINE_METRIC_CONTEXT_RE = re.compile(
    r"(?<![A-Za-z])(?:CVaR|VaR|ES)(?![A-Za-z])|Expected\s+Shortfall|"
    r"손실액|손실률|신뢰수준|보유기간|관측기간|관측치|포트폴리오\s*총액",
    flags=re.IGNORECASE,
)
_ENGINE_DATE_CONTEXT_RE = re.compile(r"기준일|산출일|데이터.{0,8}종료|관측.{0,8}종료")
_ENGINE_METRIC_TOPICS = {"VaR 해석", "스트레스 시나리오", "기준일 및 유의사항"}
_CLAUSE_BOUNDARY_RE = re.compile(r"[,.!?;\n]")
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?;\n]")
_SPACED_AN_NEGATION_RE = re.compile(r"(?:^|\s)안(?:\s|되|돼|됨|함|하)")
_CLEAR_DOUBLE_NEGATION_PATTERNS = (
    re.compile(
        r"(?:않|아니|못|없)(?:는다고|다고|라고)?[\s,]*(?:오해|착각).{0,12}"
        r"(?:안(?:\s|되|돼|됨|함|하)|않|말|마(?:십시오|세요|라|시오)|마(?=\s|[.!?]|$))"
    ),
    re.compile(
        r"(?:않|아니|못|없)(?:는다고|다고|라고)?[\s,]*(?:을|할)\s*수\s*(?:없|않)"
    ),
    re.compile(r"(?:않|아니|못|없).{0,8}(?:것|건)(?:은|이)?[\s,]*(?:아니|않)"),
)


def _explanation_text(explanations: list) -> str:
    return "\n".join(
        str(item.get("text", "")).strip()
        for item in explanations
        if isinstance(item, dict)
        and item.get("topic") != "재작성 반영"
        and str(item.get("text", "")).strip()
    )


def source_validity(citations: list, strict: bool) -> tuple[bool, str]:
    verified = [
        citation
        for citation in citations
        if isinstance(citation, dict) and citation.get("verified") is True
    ]
    if verified:
        return True, f"검증 통과 인용 {len(verified)}건"
    if strict:
        return False, "strict citation gate에서 검증 통과 인용이 0건입니다."
    return True, "검증 통과 인용이 0건이므로 수동검토 대상으로 통과합니다."


def _metric_numbers(value, *, key: str = "") -> set[float]:
    numbers: set[float] = set()
    if isinstance(value, bool):
        return numbers
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        number = float(value)
        numbers.update((number, abs(number)))
        if abs(number) <= 1:
            numbers.update((number * 100, abs(number) * 100))
        if key == "confidence" and 0 < number < 1:
            exceedance = 1 - number
            numbers.update((1.0, round(1 / exceedance)))
        return numbers
    if isinstance(value, dict):
        for child_key, child in value.items():
            match = re.fullmatch(r"(\d+)[dD]", str(child_key))
            if match:
                numbers.add(float(match.group(1)))
            numbers.update(_metric_numbers(child, key=str(child_key)))
    elif isinstance(value, (list, tuple)):
        for child in value:
            numbers.update(_metric_numbers(child, key=key))
    return numbers


def _metric_dates(value) -> set[str]:
    dates: set[str] = set()
    if isinstance(value, str):
        dates.update(_DATE_RE.findall(value))
    elif isinstance(value, dict):
        for child in value.values():
            dates.update(_metric_dates(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            dates.update(_metric_dates(child))
    return dates


def _normalized_mention(number: float, unit: str) -> float:
    if unit in ("억원", "억"):
        return number * 100_000_000
    if unit == "만원":
        return number * 10_000
    return number


def _mention_context(text: str, start: int, end: int) -> str:
    """숫자 주변의 짧은 구간을 반환해 엔진 수치 문맥인지 판별한다."""
    return text[max(0, start - 24):min(len(text), end + 16)]


def _verified_quotes_by_topic(citations: list | None) -> dict[str, list[str]]:
    """검증된 인용문을 claim(topic)별로 공백 정규화해 묶는다."""
    by_topic: dict[str, list[str]] = {}
    for citation in citations or []:
        if not isinstance(citation, dict) or citation.get("verified") is not True:
            continue
        quote = citation.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        topic = str(citation.get("claim") or "").strip()
        by_topic.setdefault(topic, []).append(" ".join(quote.split()))
    return by_topic


def _is_cited_fact(mention: str, topic: str, quotes_by_topic: dict[str, list[str]]) -> bool:
    """같은 topic 인용 quote에 숫자·날짜가 실제로 존재하는지 확인한다."""
    normalized = " ".join(mention.split())
    quotes = quotes_by_topic.get(topic, [])
    return bool(normalized) and any(normalized in quote for quote in quotes)


def numeric_consistency(
    explanations: list,
    metrics: dict,
    expected_dates: set[str] | None = None,
    citations: list | None = None,
) -> tuple[bool, str]:
    candidates = _metric_numbers(metrics)
    dates = _metric_dates(metrics) | (expected_dates or set())
    quotes_by_topic = _verified_quotes_by_topic(citations)
    mismatches: list[str] = []
    engine_metric_count = 0
    evidence_fact_count = 0

    for explanation in explanations:
        if not isinstance(explanation, dict) or explanation.get("topic") == "재작성 반영":
            continue
        topic = str(explanation.get("topic") or "").strip()
        text = str(explanation.get("text") or "").strip()
        if not text:
            continue

        for match in _DATE_RE.finditer(text):
            date = match.group(0)
            context = _mention_context(text, match.start(), match.end())
            is_engine_date = (
                topic == "기준일 및 유의사항"
                or _ENGINE_DATE_CONTEXT_RE.search(context)
            )
            if is_engine_date:
                if date in dates:
                    engine_metric_count += 1
                else:
                    mismatches.append(f"기준 데이터에 없는 날짜 {date}")
            elif _is_cited_fact(date, topic, quotes_by_topic):
                evidence_fact_count += 1
            else:
                mismatches.append(f"날짜 {date}가 같은 topic의 검증 인용에 없음")

        text_without_dates = _DATE_RE.sub("", text)
        for match in _NUMBER_RE.finditer(text_without_dates):
            raw = match.group("number")
            unit = match.group("unit") or ""
            mention = match.group(0).strip()
            number = float(raw.replace(",", ""))
            normalized = _normalized_mention(number, unit)
            context = _mention_context(
                text_without_dates,
                match.start(),
                match.end(),
            )
            is_engine_metric = (
                topic in _ENGINE_METRIC_TOPICS
                or _ENGINE_METRIC_CONTEXT_RE.search(context)
            )
            if is_engine_metric:
                metric_match = any(
                    math.isclose(normalized, candidate, rel_tol=1e-6, abs_tol=1e-6)
                    for candidate in candidates
                )
                if metric_match:
                    engine_metric_count += 1
                else:
                    mismatches.append(f"설명 수치 {raw}{unit}가 metrics에 없음")
            elif _is_cited_fact(mention, topic, quotes_by_topic):
                evidence_fact_count += 1
            else:
                mismatches.append(f"설명 수치 {raw}{unit}가 같은 topic의 검증 인용에 없음")

    if mismatches:
        return False, "; ".join(mismatches)
    return (
        True,
        "설명문의 엔진 수치·기준일은 metrics와 일치하고 인용 사실은 검증 인용과 "
        f"일치합니다. (engine_metric={engine_metric_count}, evidence_fact={evidence_fact_count})",
    )


def _response_text(response) -> str:
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)


def _parse_llm_result(raw: str) -> tuple[bool, str]:
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return False, "LLM Judge 응답에 JSON 객체가 없습니다."
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return False, "LLM Judge 응답 JSON을 해석할 수 없습니다."
    if not isinstance(payload.get("passed"), bool):
        return False, "LLM Judge 응답의 passed가 bool이 아닙니다."
    reason = str(payload.get("reason") or "사유 미제공")
    return payload["passed"], reason


def _run_llm_axis(llm, *, axis: str, instruction: str, payload: dict) -> tuple[bool, str]:
    if llm is None:
        return False, f"{axis} 판정을 위한 LLM Judge를 구성하지 못했습니다."
    prompt = (
        "너는 리스크 리포트의 품질 심사자다. 제공된 자료 밖의 지식을 사용하지 마라.\n"
        f"판정 축: {axis}\n판정 규칙: {instruction}\n"
        '반드시 {"passed": true 또는 false, "reason": "구체적 사유"} JSON만 출력하라.\n'
        "입력:\n" + json.dumps(payload, ensure_ascii=False, default=str)
    )
    try:
        return _parse_llm_result(_response_text(llm.invoke(prompt)))
    except Exception as exc:
        return False, f"LLM Judge 호출 실패: {type(exc).__name__}: {exc}"


def hallucination(
    explanations: list,
    citations: list,
    llm,
    expected_dates: set[str] | None = None,
) -> tuple[bool, str]:
    evidence = [
        {
            "claim": citation.get("claim", ""),
            "quote": citation.get("quote", ""),
            "source": citation.get("source", ""),
            "chunk_id": citation.get("chunk_id", ""),
            "chunk_text": (citation.get("extra") or {}).get("chunk_text", ""),
        }
        for citation in citations
        if isinstance(citation, dict) and citation.get("verified") is True
    ]
    return _run_llm_axis(
        llm,
        axis="hallucination",
        instruction=(
            "설명문의 실질적 주장 중 검증된 인용문 또는 해당 청크 원문으로 "
            "뒷받침되지 않는 주장이 하나라도 있으면 fail한다. 단, deterministic_context의 "
            "expected_dates에 있는 기준일은 state에서 검증된 값이며, 투자 권유·수익 보장이 "
            "아니라는 의무 면책문은 외부 사실 주장이 아니므로 인용 부재만으로 fail하지 않는다."
        ),
        payload={
            "explanations": _explanation_text(explanations),
            "citations": evidence,
            "deterministic_context": {
                "expected_dates": sorted(expected_dates or set()),
            },
        },
    )


def false_precision(explanations: list, llm) -> tuple[bool, str]:
    return _run_llm_axis(
        llm,
        axis="false_precision",
        instruction=(
            "확률·손실을 근거 없이 정밀하게 단정하면 fail한다. 신뢰수준과 보유기간을 "
            "명시한 VaR, 또는 약·추정·범위·신뢰구간 표현은 허용한다."
        ),
        payload={"explanations": _explanation_text(explanations)},
    )


def disclaimer(
    explanations: list,
    expected_dates: set[str] | None = None,
) -> tuple[bool, str]:
    text = _explanation_text(explanations)
    dates = set(_DATE_RE.findall(text))
    expected = expected_dates or set()
    date_ok = bool(dates & expected) if expected else bool(dates)
    disclaimer_patterns = (
        r"투자\s*권유.{0,12}(?:아니|않)",
        r"보장.{0,15}(?:않|아니|못|없)",
        r"실제\s*결과.{0,12}다를\s*수",
    )
    disclaimer_ok = any(re.search(pattern, text) for pattern in disclaimer_patterns)
    missing: list[str] = []
    if not date_ok:
        missing.append("state와 일치하는 기준일")
    if not disclaimer_ok:
        missing.append("투자 권유·손실 가능성 면책 문구")
    if missing:
        return False, "누락: " + ", ".join(missing)
    return True, "기준일과 면책 문구가 존재합니다."


def _scan_prohibited(explanations: list) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    ambiguous: list[str] = []
    text = _explanation_text(explanations)
    for term in PROHIBITED_TERMS:
        for match in re.finditer(re.escape(term), text):
            context = text[match.end() : match.end() + NEGATION_WINDOW]
            extended_context = text[match.end() : match.end() + DOUBLE_NEGATION_WINDOW]
            context = _CLAUSE_BOUNDARY_RE.split(context, maxsplit=1)[0]
            extended_context = _SENTENCE_BOUNDARY_RE.split(extended_context, maxsplit=1)[0]
            negations = [marker for marker in NEGATION_MARKERS if marker in context]
            if _SPACED_AN_NEGATION_RE.search(context):
                negations.append("안")
            clear_double_negation = any(
                pattern.search(extended_context)
                for pattern in _CLEAR_DOUBLE_NEGATION_PATTERNS
            )
            if clear_double_negation:
                violations.append(
                    f"{term} 뒤 명시적 이중부정: {extended_context.strip()[:40]}"
                )
            elif not negations:
                violations.append(f"{term}({context.strip()[:20]})")
            elif len(negations) > 1:
                ambiguous.append(f"{term} 뒤 이중부정 가능성: {context.strip()[:20]}")
    return violations, ambiguous


def prohibited_expression(explanations: list) -> tuple[bool, str]:
    violations, ambiguous = _scan_prohibited(explanations)
    if violations:
        return False, "금지 표현의 긍정적 사용: " + ", ".join(violations)
    if ambiguous:
        return True, "자동 실패 대신 수동검토: " + "; ".join(ambiguous)
    return True, "금지 표현이 없거나 명시적으로 부정되었습니다."


def prohibited_manual_flags(explanations: list) -> list[str]:
    _, ambiguous = _scan_prohibited(explanations)
    return ["금지 표현 문맥 수동검토: " + item for item in ambiguous]


def evaluate_rubric(
    *,
    explanations: list,
    citations: list,
    metrics: dict,
    strict_citation_gate: bool,
    expected_dates: set[str],
    llm,
) -> tuple[dict[str, tuple[bool, str]], list[str]]:
    results = {
        "source_validity": source_validity(citations, strict_citation_gate),
        "numeric_consistency": numeric_consistency(
            explanations,
            metrics,
            expected_dates,
            citations,
        ),
        "hallucination": hallucination(
            explanations,
            citations,
            llm,
            expected_dates,
        ),
        "false_precision": false_precision(explanations, llm),
        "disclaimer": disclaimer(explanations, expected_dates),
        "prohibited_expression": prohibited_expression(explanations),
    }
    return results, prohibited_manual_flags(explanations)
