# AGENTS.md — 재현가능·설명가능 리스크 리포트 엔진

이 레포에서 일하는 모든 AI 에이전트(코드 어시스턴트)는 이 문서의 규칙을 따른다.

## 프로젝트 한 줄 소개

삼성증권 영크리에이터 15기 4조 · 과제2 — LangGraph 기반 재현가능·설명가능 리스크 리포트 엔진.

## 기술 스택

| 영역 | 사용 기술 |
| --- | --- |
| 오케스트레이션 | LangGraph (StateGraph, MemorySaver, HITL 인터럽트) |
| LLM | Azure OpenAI (LangChain, temperature=0) — 현재 스텁 |
| 결정론 엔진 | numpy/scipy — historical VaR/CVaR, 스트레스 테스트 |
| 관측성 | LangSmith 트레이싱 |
| UI | Streamlit |
| 협업 | GitHub, Notion, Slack |

## 레포 구조

```
Orchestration/
├── app/
│   ├── state.py       # RiskState/IPSProfile — 팀 데이터 계약(SSOT), 임의 수정 금지
│   ├── graph.py       # StateGraph 조립 (8노드 + 조건부 엣지 3개)
│   ├── nodes/         # 그래프 노드 (순수 함수, 바꾼 키만 반환)
│   ├── engine/        # 결정론 계층 — langchain/llm import 금지
│   ├── llm/           # AzureChatOpenAI 팩토리
│   └── utils/         # 해시 등 공용 유틸
├── config/            # config.yaml (seed, as_of_date, VaR 설정)
├── corpus/            # RAG 근거 문서 (예정)
├── data/              # 시장 데이터 (gitignore 대상 산출물 포함)
├── scripts/           # CLI 진입점 (run_graph.py)
├── tests/             # pytest
├── ui/                # Streamlit UI
└── .github/           # PR 템플릿·CI 등 GitHub 관련 설정
```

## 작업 규칙

- **계층 경계**: `app/engine/`(결정론 계층)에는 langchain/openai 등 LLM 관련 import를 절대 추가하지 않는다. LLM 호출은 `app/llm/` + 노드 계층에서만 한다.
- **데이터 계약**: `app/state.py`의 `RiskState`/`IPSProfile`은 팀 합의 없이 수정하지 않는다.
- **재현성**: 노드는 결정론적으로 동작해야 하며(랜덤 시드 고정), 계산 결과에는 computation_hash를 남긴다.
- **커밋 메시지**: 한국어로, `타입: 설명` 형식. 타입은 `feat`, `fix`, `docs`, `chore`, `refactor`, `test` 중 하나.
  - 예) `feat: 스트레스 시나리오 금리 충격 추가`
- **빌드/실행 확인**: 푸시 전 반드시 로컬에서 `python scripts/run_graph.py --auto-approve` 완주와 `pytest` 통과를 확인한다.
- **덮어쓰기 알림**: 기존 파일을 지우거나 덮어쓰기 전 사용자에게 먼저 알리고 동의를 받는다.
- **비밀 정보 금지**: `.env` 파일과 모든 비밀키는 절대 커밋하지 않는다. `.env.example`만 추적 대상이다.

## 브랜치 전략

- GitFlow: `feature/* → develop → main`. `main` 직접 커밋 금지, 모든 변경은 PR + 리뷰 1명.
