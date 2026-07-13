"""HITL 승인 게이트: reviewed 입력을 검증한 뒤에만 locked로 전이한다."""
from __future__ import annotations

from app.state import RiskState
from app.utils.hashing import sha256_of_dict


def _conflicts_by_severity(conflicts: list) -> tuple[list[dict], list[dict]]:
    blocking = [item for item in conflicts if item.get("severity") == "block"]
    review = [item for item in conflicts if item.get("severity") == "review"]
    return blocking, review


def approval_gate(state: RiskState) -> dict:
    """PB 검토 결과와 예외 승인 기준을 검증하고 승인 레코드를 잠근다.

    - block 충돌: 예외 승인 불가
    - review 충돌: exception_approved + 구체적 사유 필요
    - 충돌 없음: approved만 허용
    """
    approval = dict(state.get("approval") or {})
    conflicts = state.get("conflicts") or []
    blocking, review = _conflicts_by_severity(conflicts)

    if approval.get("status") != "reviewed":
        raise ValueError("approval은 draft에서 PB reviewed 상태를 거쳐야 합니다.")
    if not str(approval.get("approver") or "").strip():
        raise ValueError("PB 승인자 정보가 필요합니다.")
    if blocking:
        rules = ", ".join(sorted({item.get("rule", "unknown") for item in blocking}))
        raise ValueError(f"예외 승인할 수 없는 block 충돌이 있습니다: {rules}")

    decision = approval.get("decision")
    if review:
        if decision != "exception_approved":
            raise ValueError("review 충돌은 PB 예외 승인으로만 계산을 진행할 수 있습니다.")
        exception_reason = str(approval.get("exception_reason") or "").strip()
        if len(exception_reason) < 10:
            raise ValueError("예외 승인 사유를 10자 이상 구체적으로 입력해야 합니다.")
        approval["exception_reason"] = exception_reason
    elif decision != "approved":
        raise ValueError("충돌이 없는 경우 일반 승인(approved)만 허용합니다.")

    as_of_date = (state.get("run_config") or {}).get("as_of_date")
    approval.update(
        {
            "status": "locked",
            "reviewed_as_of": approval.get("reviewed_as_of") or as_of_date,
            "locked_as_of": as_of_date,
            "scope": "risk_calculation_only",
            "trade_approval": False,
            "unresolved_conflicts": conflicts,
        }
    )
    approval["approval_hash"] = sha256_of_dict(approval)
    return {"approval": approval}
