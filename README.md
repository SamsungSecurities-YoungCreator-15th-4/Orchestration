# Orchestration
재현가능·설명가능 리스크 리포트 엔진

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

## 실행 흐름

`load_inputs → extract_ips → conflict_check → approval_gate(HITL) → var_engine →`
`rag_cite → judge_eval → assemble_report`

- `var_engine`은 Historical VaR·CVaR, 신뢰구간과 3개 스트레스 시나리오를 계산한다.
- `rag_cite`는 상태를 기준으로 corpus category를 자동 라우팅하고 Chroma metadata
  filter를 적용한 뒤 원문 부분문자열만 인용한다. `methodology`와 `macro`는 항상,
  `house_view`는 CVaR 기여 상위 자산군이 있을 때, `tax`는 IPS에 실질 세무 이슈가
  있을 때만 검색한다. 인용에는 계산 근거/해석 참고 역할, 라우팅 사유와 파일명 기반
  발행일을 기록한다. 시장·세무 문서는 정량 계산 입력이 아니라 해석 참고로 사용한다.
- `judge_eval`은 6축 루브릭과 인용 감사 계약을 검사한다. 잘못된 역할·라우팅은
  차단하고, 발행일 누락이나 6개월 초과 house view는 수동검토 경고로 남긴다.
  재작성은 최대 2회 시도 후 수동검토로 전환한다.
- Streamlit의 RAG 근거는 정량 방법론·거시/스트레스·자산시장·세무의 4개 역할별
  표로 나누며, 고객에게는 설명주제·근거문장·출처·발행기준일만 표시한다.
- LangSmith는 APAC 프로젝트에서 HITL 전후 trace와 감사정보를 기록한다. 기본 설정은
  입력·출력을 숨겨 상담정보를 외부 trace에 남기지 않는다.

## 실행법
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_graph.py --auto-approve --offline
pytest
```

로컬 `.env`와 시장데이터 캐시·Chroma가 준비된 실제 실행:

```bash
python -m app.rag.ingest
python scripts/smoke_rag.py
python scripts/run_graph.py --auto-approve
streamlit run ui/app.py
```

통합·배포 전에는 API 키 값을 출력하지 않는 사전점검을 실행한다.

```bash
python scripts/preflight_release.py
python scripts/preflight_release.py --real  # 실제 Azure E2E 포함
```

gpt-4o IPS 추출의 20개 회귀 사례 정확도와 동일 입력 반복 일치율은 Azure 키가 있는
환경에서 다음 명령으로 별도 측정한다.

```bash
python scripts/evaluate_ips_extraction.py --repeats 3
```

IPS 충돌·예외 승인 기준은 [`docs/ips_conflict_policy.md`](docs/ips_conflict_policy.md)에
공식 근거, 내부 임계값, `draft → reviewed → locked` 계약과 함께 기록한다.
20사례×3회 실제 평가 결과는
[`docs/ips_extraction_evaluation.md`](docs/ips_extraction_evaluation.md)에 기록한다.

Judge 평가셋은 결정론 15건과 Azure LLM 5건을 분리한다.

```bash
pytest tests/test_judge_eval_evalset.py
RUN_AZURE_JUDGE_EVALSET=1 pytest tests/test_judge_eval_evalset.py
```

## 로컬 자산과 비밀정보

- `.env`에는 Azure OpenAI와 LangSmith 키를 두되 git에 커밋하지 않는다.
- 코퍼스 PDF 21건과 `data/chroma/`, 실데이터 parquet는 로컬 전용이다.
- 추적 가능한 문서 목록은 [`corpus/manifest.md`](corpus/manifest.md)에 유지한다.
- Streamlit 배포에는 private Azure Blob의 검증된 Chroma 아티팩트를 사용한다.
  생성·업로드·Secrets 설정은
  [`docs/rag_index_deployment.md`](docs/rag_index_deployment.md)를 따른다.
- 제출·시연에서는 `config/config.yaml`의 `strict_citation_gate`를 `true`로 전환한다.

## 브랜치 규칙
- `main` ← `develop` ← `feature/*`
- 기능 작업은 `feature/<이름>` 브랜치에서 수행 후 `develop`으로 PR
