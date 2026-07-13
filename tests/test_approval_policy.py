"""충돌 severity와 draft→reviewed→locked 승인 계약 테스트."""
import pytest

from app.nodes.approval_gate import approval_gate


BASE = {"run_config": {"as_of_date": "2026-07-03"}}


def test_normal_approval_requires_reviewed_then_locks():
    result = approval_gate(
        {
            **BASE,
            "conflicts": [],
            "approval": {
                "status": "reviewed",
                "decision": "approved",
                "approver": "PB-001",
                "note": "적합성 확인",
            },
        }
    )

    approval = result["approval"]
    assert approval["status"] == "locked"
    assert approval["decision"] == "approved"
    assert approval["trade_approval"] is False
    assert approval["approval_hash"]


def test_draft_cannot_skip_reviewed_state():
    with pytest.raises(ValueError, match="reviewed"):
        approval_gate(
            {
                **BASE,
                "conflicts": [],
                "approval": {"status": "draft", "approver": "PB-001"},
            }
        )


def test_review_conflict_requires_exception_reason():
    conflict = {"rule": "liquidity_cash_shortfall", "severity": "review"}
    with pytest.raises(ValueError, match="예외 승인"):
        approval_gate(
            {
                **BASE,
                "conflicts": [conflict],
                "approval": {
                    "status": "reviewed",
                    "decision": "approved",
                    "approver": "PB-001",
                },
            }
        )

    result = approval_gate(
        {
            **BASE,
            "conflicts": [conflict],
            "approval": {
                "status": "reviewed",
                "decision": "exception_approved",
                "approver": "PB-001",
                "exception_reason": "현금성 자산을 추가 확보한 뒤 계산 결과만 검토",
            },
        }
    )
    assert result["approval"]["status"] == "locked"


def test_block_conflict_cannot_be_exception_approved():
    with pytest.raises(ValueError, match="block"):
        approval_gate(
            {
                **BASE,
                "conflicts": [{"rule": "time_horizon_missing", "severity": "block"}],
                "approval": {
                    "status": "reviewed",
                    "decision": "exception_approved",
                    "approver": "PB-001",
                    "exception_reason": "시연 목적이지만 충분히 긴 예외 승인 사유",
                },
            }
        )
