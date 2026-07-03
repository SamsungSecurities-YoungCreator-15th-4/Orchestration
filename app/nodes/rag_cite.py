"""설명·인용 생성 — 현재는 고정 스텁.

TODO: corpus/ 기반 retriever 연결 후 실제 근거 문서 인용으로 교체.
"""
from app.state import RiskState


def rag_cite(state: RiskState) -> dict:
    revision = state.get("judge_retries", 0)  # judge 루프 재작성 횟수 표시용
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
    citations = [
        {"doc_id": "stub-001", "title": "리스크 지표 산출 기준서(내부)", "loc": "§3.2"},
        {"doc_id": "stub-002", "title": "스트레스 테스트 시나리오 정의서(내부)", "loc": "§1.1"},
    ]
    return {"explanations": explanations, "citations": citations}
