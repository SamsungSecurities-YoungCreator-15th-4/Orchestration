"""LLM-as-judge 평가 — 현재는 스텁.

RISK_FORCE_JUDGE_FAIL 환경변수(정수 N)만큼 강제로 실패시켜
judge 재작성 루프를 시연할 수 있다. N회 실패 후에는 통과한다.

TODO: 실제 LLM judge(app.llm.client.get_llm) 연결.
"""
import os

from app.state import RiskState


def judge_eval(state: RiskState) -> dict:
    retries = state.get("judge_retries", 0) + 1
    force_fail_n = int(os.environ.get("RISK_FORCE_JUDGE_FAIL", "0"))

    if retries <= force_fail_n:
        reason = f"[강제실패 {retries}/{force_fail_n}] 인용 근거가 설명 문장과 1:1로 연결되지 않음 — 재작성 필요"
        return {
            "judge_retries": retries,
            "judge": {"passed": False, "score": 0.4, "reason": reason},
            "judge_feedback": reason,
        }

    return {
        "judge_retries": retries,
        "judge": {
            "passed": True,
            "score": 0.92,
            "reason": "설명-인용 정합성 및 수치 일치 확인 (스텁 평가)",
        },
        "judge_feedback": "",
    }
