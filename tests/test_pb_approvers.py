"""Streamlit PB 이름·사번 후보 및 승인 권한 검증 테스트."""

import pytest

from ui.pb_approvers import (
    AUTHORIZED_PB,
    PB_CANDIDATES,
    approver_label,
    candidate_rows,
    validate_pb_approver,
)


def test_pb_candidate_contract_is_fixed():
    assert PB_CANDIDATES == (
        ("국준호", "010904"),
        ("고다경", "030715"),
        ("나승민", "010518"),
        ("오지은", "050116"),
        ("최중현", "010726"),
    )
    assert AUTHORIZED_PB == ("나승민", "010518")
    assert candidate_rows()[2] == {"PB 이름": "나승민", "PB 사번": "010518"}


def test_only_authorized_pb_pair_is_accepted():
    assert validate_pb_approver("나승민", "010518") is None
    assert validate_pb_approver("  나승민  ", " 010518 ") is None
    assert approver_label(" 나승민 ", " 010518 ") == "나승민 / 010518"


@pytest.mark.parametrize(
    ("name", "employee_id"),
    [
        ("", "010518"),
        ("나승민", ""),
        ("없는PB", "010518"),
        ("나승민", "999999"),
    ],
)
def test_missing_or_unknown_pb_information_is_rejected(name, employee_id):
    assert validate_pb_approver(name, employee_id)


def test_mismatched_candidate_name_and_employee_id_are_rejected():
    error = validate_pb_approver("국준호", "010518")
    assert error == "PB 이름과 PB 사번의 매칭이 일치하지 않습니다."


@pytest.mark.parametrize(
    ("name", "employee_id"),
    [pair for pair in PB_CANDIDATES if pair != AUTHORIZED_PB],
)
def test_matching_but_unauthorized_pb_candidates_are_rejected(name, employee_id):
    assert validate_pb_approver(name, employee_id) == "해당 PB는 승인 권한이 없습니다."
