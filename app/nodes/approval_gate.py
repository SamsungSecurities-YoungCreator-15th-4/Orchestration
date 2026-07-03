"""HITL 승인 게이트 — interrupt_before로 이 노드 직전에 그래프가 멈춘다.

사람이(또는 CLI --auto-approve가) approval을 주입한 뒤 재개되면,
이 노드는 승인 내용을 잠금(locked) 처리해 이후 단계에서 변경 불가로 만든다.
미해결 충돌은 approval에 첨부되어 사람 판단의 근거로 남긴다.
"""
from app.state import RiskState


def approval_gate(state: RiskState) -> dict:
    approval = dict(state.get("approval", {}))
    approval.update(
        {
            "status": "locked",
            "locked_as_of": state.get("run_config", {}).get("as_of_date"),
            "unresolved_conflicts": state.get("conflicts", []),
        }
    )
    return {"approval": approval}
