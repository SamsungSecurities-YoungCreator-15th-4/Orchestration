"""Streamlit PB 승인자 후보와 UI 전용 승인 검증 규칙."""

from __future__ import annotations


PB_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("국준호", "010904"),
    ("고다경", "030715"),
    ("나승민", "010518"),
    ("오지은", "050116"),
    ("최중현", "010726"),
)
AUTHORIZED_PB = ("나승민", "010518")
_CANDIDATES_DICT = dict(PB_CANDIDATES)


def validate_pb_approver(name: str | None, employee_id: str | None) -> str | None:
    """입력한 PB가 후보 명단의 유일한 승인 가능 조합인지 검사한다."""
    normalized_name = (name or "").strip()
    normalized_id = (employee_id or "").strip()
    if not normalized_name or not normalized_id:
        return "PB 이름과 PB 사번을 모두 입력해야 합니다."

    expected_id = _CANDIDATES_DICT.get(normalized_name)
    if expected_id is None:
        return "등록된 PB 후보 정보와 일치하지 않습니다."
    if expected_id != normalized_id:
        return "PB 이름과 PB 사번의 매칭이 일치하지 않습니다."
    if (normalized_name, normalized_id) != AUTHORIZED_PB:
        return "해당 PB는 승인 권한이 없습니다."
    return None


def approver_label(name: str | None, employee_id: str | None) -> str:
    """기존 ApprovalRecord.approver 필드에 저장할 감사용 식별 문자열을 만든다."""
    return f"{(name or '').strip()} / {(employee_id or '').strip()}"
