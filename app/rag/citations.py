"""인용 검증 — 순수 결정론 계층.

★이 파일에는 langchain/openai 등 LLM 관련 import를 절대 추가하지 않는다.★
LLM은 인용 후보를 "만들" 수는 있어도, 이 검증을 "통과시킬" 수는 없다:
인용문이 해당 chunk_id 원문의 실제 부분문자열일 때만 verified=True가 된다.
같은 입력이면 항상 같은 결과가 나온다(재현성).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Citation:
    """수치·주장 하나를 뒷받침하는 인용."""

    quote: str          # 원문에서 그대로 따온 인용문
    source: str         # 파일명 (청크 metadata의 source)
    chunk_id: str       # 인용이 속한 청크 id
    verified: bool = False
    claim: str = ""     # 이 인용이 뒷받침하는 수치/주장 (선택)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "quote": self.quote,
            "source": self.source,
            "chunk_id": self.chunk_id,
            "verified": self.verified,
            "claim": self.claim,
            **({"extra": self.extra} if self.extra else {}),
        }


def normalize_ws(text: str) -> str:
    """공백 정규화 — 모든 연속 공백(개행 포함)을 단일 스페이스로."""
    return " ".join(text.split())


def verify_citations(
    citations: list[Citation],
    chunks: list[dict],
) -> tuple[list[Citation], list[dict]]:
    """각 인용문이 해당 chunk_id 원문에 실제로 존재하는지 검증한다.

    Args:
        citations: 검증할 인용 후보 목록.
        chunks: 검색된 청크 목록. 각 항목은 최소 {"chunk_id": str, "text": str}.

    Returns:
        (verified, rejected)
        - verified: 검증을 통과한 Citation 목록 (verified=True로 마킹).
        - rejected: 탈락 인용의 로그용 목록 [{"quote", "source", "chunk_id", "reason"}].

    검증 규칙(순수 결정론):
    - chunk_id가 chunks에 존재해야 한다.
    - 공백 정규화 후, 인용문이 청크 원문의 부분문자열이어야 한다.
    - 빈 인용문은 탈락.
    """
    text_by_id = {c["chunk_id"]: c.get("text", "") for c in chunks}

    verified: list[Citation] = []
    rejected: list[dict] = []

    for cit in citations:
        reason = None
        norm_quote = normalize_ws(cit.quote)

        if not norm_quote:
            reason = "빈 인용문"
        elif cit.chunk_id not in text_by_id:
            reason = f"존재하지 않는 chunk_id: {cit.chunk_id}"
        elif norm_quote not in normalize_ws(text_by_id[cit.chunk_id]):
            reason = "인용문이 청크 원문에 없음(환각 의심)"

        if reason is None:
            cit.verified = True
            verified.append(cit)
        else:
            cit.verified = False
            rejected.append(
                {
                    "quote": cit.quote,
                    "source": cit.source,
                    "chunk_id": cit.chunk_id,
                    "reason": reason,
                }
            )

    return verified, rejected
