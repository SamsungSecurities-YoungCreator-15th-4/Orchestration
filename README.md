# Orchestration
재현가능·설명가능 리스크 리포트 엔진 (LangGraph 스켈레톤)

## 문제 정의

고금리·강달러 국면에서 PB가 고액 자산가 고객에게 리스크 리포트를 제시할 때,
같은 입력에도 매번 다른 수치가 나오거나(재현 불가) 근거를 대지 못하면(설명 불가)
신뢰가 무너진다. 예시 페르소나: **50대 자영업자, 위탁자산 50억, 6개 자산군 분산**.

이 프로젝트는 그 문제를 **재현가능성**과 **설명가능성** 두 축으로 해결한다.

- **재현가능성** — VaR·CVaR·스트레스 계산을 결정론(numpy) 계층으로 격리하고
  시드를 고정, 결과에 `computation_hash`를 남겨 "같은 입력 → 같은 리포트"를 보장한다.
- **설명가능성** — RAG 근거 인용, PB 승인 게이트(HITL), judge 자동 평가 루프를
  LangGraph 흐름에 배치해 각 수치가 "어디서 왔고 누가 승인했는지"를 추적 가능하게 한다.

핵심 설계 원칙은 **통제가 필요한 곳(분기·승인·루프)엔 LangGraph를, 재현이 필요한
곳(수치 계산)엔 결정론 엔진을** 분리 배치하는 것이다.

## 실행법
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_graph.py --auto-approve
pytest
```

## 브랜치 규칙
- `main` ← `develop` ← `feature/*`
- 기능 작업은 `feature/<이름>` 브랜치에서 수행 후 `develop`으로 PR
