# AGENTS.md — 재현가능·설명가능 리스크 리포트 엔진

이 문서는 Codex·Claude Code 등 AI 코딩 에이전트가 세션 시작 시 읽는 공용 컨텍스트다.
이 레포에서 일하는 모든 에이전트는 새 작업을 시작하기 전 이 문서를 먼저 읽고,
아래 불변 규칙을 위반하지 않는다.

## 불변 규칙 (위반 금지)

1. **재현성** — 노드는 결정론적으로 동작한다. 무작위성이 있으면 시드를 고정하고,
   같은 입력이면 같은 결과가 나와야 한다. 계산 결과에는 computation_hash를 남긴다.
2. **설명가능(화이트박스)** — 모든 수치·주장에는 근거(citations/evidence)를 첨부한다.
   최종 판단은 사람이 한다(HITL, `approval_gate` 직전 인터럽트).
3. **계층 경계** — `app/engine/`(결정론 계층) 안에서는 langchain·openai import를 금지한다.
   수치 계산은 순수 파이썬/numpy로만 한다. LLM 호출은 `app/llm/` + 노드 계층에서만 한다.
4. **데이터 계약** — `app/state.py`(`RiskState`/`IPSProfile`)는 팀 합의 없이 수정하지 않는다.
   변경이 필요하면 먼저 팀에 공유한다.

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
├── corpus/            # RAG 근거 문서 (19건, 원문 PDF는 gitignore·로컬 전용 / manifest.md 참조)
├── data/              # 시장 데이터 (gitignore 대상 산출물 포함)
├── scripts/           # CLI 진입점 (run_graph.py)
├── tests/             # pytest
├── ui/                # Streamlit UI
└── .github/           # PR 템플릿·CI 등 GitHub 관련 설정
```

## 그래프 노드 흐름

`app/graph.py`의 실제 조립 기준. 노드 8개, 조건부 분기 2개, HITL 인터럽트 1개.

```
START
  → load_inputs
  → extract_ips  ◄──────────────┐  (분기① 충돌 재추출 루프)
  → conflict_check ──────────────┘
        │  route_after_conflict_check:
        │   conflicts 있고 conflict_retries < MAX_CONFLICT_RETRIES(=1) → extract_ips 회귀
        │   그 외 → approval_gate
        ▼
  → approval_gate        ★ HITL: interrupt_before=["approval_gate"] (사람 승인 대기)
  → var_engine
  → rag_cite  ◄──────────────────┐  (분기③ judge 재작성 루프)
  → judge_eval ───────────────────┘
        │  route_after_judge:
        │   judge.passed 또는 judge_retries >= MAX_JUDGE_RETRIES(=3) → assemble_report
        │   그 외 → rag_cite 재작성
        ▼
  → assemble_report
  → END
```

- 컴파일: `g.compile(checkpointer=MemorySaver(), interrupt_before=["approval_gate"])`
- 노드는 순수 함수로, 바꾼 키만 반환한다(레포 구조의 `nodes/` 규약과 동일).

## RiskState 데이터 계약 키

`app/state.py`의 정의를 철자·타입 그대로 옮긴 것. **이 표는 SSOT가 아니라 요약 참조이며,
실제 계약은 항상 `app/state.py`가 우선한다.** 키를 추가·변경하려면 state.py를 먼저 고치고 팀에 공유한다.

### `RiskState` (`TypedDict, total=False`)

| 키 | 타입 | 생산/소비 노드 (graph.py 근거) |
| --- | --- | --- |
| `run_config` | `dict` | TBD (노드 구현 범위) |
| `trace_id` | `str` | TBD |
| `raw_input` | `str` | TBD |
| `portfolio` | `list` | TBD |
| `liquidity_required_krw` | `float \| None` | `extract_ips`가 자연어의 명시적 유동성 필요 금액을 원 단위로 저장 |
| `market_data_ref` | `dict` | TBD |
| `ips` | `dict` | TBD |
| `conflicts` | `list` | `route_after_conflict_check`가 읽어 분기① 판단 |
| `conflict_retries` | `int` | `route_after_conflict_check`가 읽어 분기① 판단 (MAX=1) |
| `approval` | `dict` | TBD (HITL `approval_gate` 관련) |
| `metrics` | `dict` | TBD |
| `explanations` | `list` | TBD |
| `citations` | `list` | TBD |
| `judge` | `dict` | `route_after_judge`가 `judge.passed`를 읽어 분기③ 판단 |
| `judge_retries` | `int` | `route_after_judge`가 읽어 분기③ 판단 (MAX=3) |
| `judge_feedback` | `str` | TBD |
| `report` | `dict` | TBD |

> "생산/소비 노드"는 `app/graph.py`로 증명되는 것만 표기했다. TBD는 각 노드 구현
> (`app/nodes/*.py`)에서 정해지며, 이 문서에서 추정하지 않는다. 정확한 소유 노드는 해당 노드 코드를 확인한다.

### `IPSProfile` (pydantic `BaseModel`)

고객 상담용 공개 IPS JSON은 `Name`, `Age`, `Job`, `Goal`, `Asset`, `Return`, `Risk`,
`Time`, `Tax`, `Liquidity`, `Legal`, `Unique` 12개 필드로 구성한다. `Age="50"`,
`Goal="시장리스크 진단·대응안을 엔진으로 산출·검증"`, `Asset=50.0`(억 원),
`Risk="균형형"`은 과제 시나리오 고정값이다. `Unique`는 항상
`"고금리·강달러 충격"`으로 시작한다.

## 코퍼스 규격

RAG 근거 문서는 `corpus/`에 카테고리별로 둔다. 상세 목록은 [`corpus/manifest.md`](corpus/manifest.md) 참조.

- 카테고리 3종: `house_view`(삼성증권 하우스뷰), `macro`(거시·통화정책), `tax`(세무).
- 총 **19건** (house_view 6 · macro 7 · tax 6).
- **원문 PDF는 저작권상 로컬 전용**이며 git에 포함하지 않는다
  (`.gitignore: /corpus/**/*.pdf`, 단 `!/corpus/**/.gitkeep`로 폴더 구조는 유지).
- git이 추적하는 것은 **폴더 구조(`.gitkeep`)와 `corpus/manifest.md`뿐**이다.
  manifest는 원문이 아닌 문서 목록이라 커밋 가능하다.

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
