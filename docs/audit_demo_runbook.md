# 모의 감사 런북 — 5분 증거 제시 · 3분 재실행

> 대상: 8/7 모의 감사 현장 진행 · 담당: 발표자 1명 + 보조 1명
> 이 문서는 대본이 아니라 **체크리스트**다. 팀 누구나 이 순서를 그대로 밟을 수 있어야 한다.
> 시간 수치는 전부 [`docs/reproducibility_scope.md`](reproducibility_scope.md) §4.1·§4.2·§7의 실측 인용이며,
> 이 문서에서 새로 측정한 값은 없다(§7 참조).

---

## 1. 전제 — 감사자가 무엇을 지정하고 무엇을 보는가

**감사자가 하는 것**: 제출된 번들 3건 중 **1건을 지목**한다. 그 뒤 5분간 증거를 제시받고, 3분간 라이브 재실행을 본다.

**번들 3건의 구성** ([`docs/symphony_proof_plan.md`](symphony_proof_plan.md) §2 R5 「모의 감사 대응」 기준):

| 번들 | 내용 | 생성 명령 |
| --- | --- | --- |
| 성공① | 정상 실행 (judge 첫 시도 통과) | `python scripts/run_graph.py --auto-approve --evidence-bundle` |
| 성공② | judge 실패 → 재작성 → 통과 (루프 시연) | 위 명령 + `--force-judge-fail N` (N은 `judge_max_retries`**보다 작은** 값) |
| 차단 | 재시도 소진 → `manual_review_gate` 정지 | 위 명령 + `--force-judge-fail N` (N은 `judge_max_retries` **이상**) |

- 출력 루트는 `--evidence-bundle`에 인자를 주지 않으면 `evidence/`다 — `scripts/run_graph.py:118` (DEFAULT_EVIDENCE_ROOT).
- 재시도 상한 숫자는 이 문서에 적지 않는다. 유일한 원천은 `config/config.yaml`의 `judge_max_retries`이고
  코드는 `app/nodes/judge_eval.py:28` (resolve_max_judge_retries)로만 읽는다.
- 강제 실패 횟수는 `demo_options.force_judge_fail`로 들어간다 — `scripts/run_graph.py:136` (force_judge_fail).

**감사자가 보는 것**: 번들 디렉터리 안의 파일 9종. 목록의 원천은 `app/evidence/schema.py:48` (BUNDLE_FILENAMES)이다.
성공 번들이든 차단 번들이든 **9종이 전부 생성된다** — 차단 사례도 제출물이다.

---

## 2. 5분 증거 제시 동선 (합 300초)

지목받은 번들 디렉터리에서 시작한다. 아래 순서를 바꾸지 않는다.

| # | 구간 | 목표 | 무엇을 연다 | 무엇을 짚는다 |
| --- | --- | --- | --- | --- |
| ① | 번들 정체 확인 | 30초 | `manifest.json` | `run_id` · `schema_version` · `generated_by.script` · `generated_by.git_sha` |
| ② | 판정 7줄 | 60초 | `summary.md` 머리말 | 아래 7줄을 위에서 아래로 그대로 읽는다 |
| ③ | 실패 축·차단 사유 | 45초 | `summary.md` §실패 축 · §차단 사유 | 성공 번들이면 "없음 (필수 검사 전부 통과)" 한 줄로 끝낸다 |
| ④ | 재현 지문 3종 | 60초 | `summary.md` §주요 해시 + `replay_diff.json` | 아래 표 참조 — **두 파일을 모두 연다** |
| ⑤ | 지목 번들 심화 1건 | 60초 | 성공 → `judge_rationale.json` / 차단 → `hard_stop_record.json` | 아래 참조 |
| ⑥ | `available:false` 선제 설명 | 30초 | 해당 파일 | 아래 §2.3 |
| — | 예비 | 15초 | — | 질문 1건 흡수용 |

**합계 300초.**

### 2.1 ② 구간 — `summary.md` 머리말 7줄

`scripts/make_evidence_bundle.py:392` (build_summary_md)가 찍는 순서 그대로다. 순서를 바꾸지 않는다.

```
- 스키마 버전:
- 기준일(as_of_date):
- judge 통과:
- 리포트 확정(finalized):
- 리포트 상태:
- 차단(hard stop):            ← 예 / 아니오
- 고객 제공·다운로드 허용(export_allowed):
```

마지막 두 줄이 무효 조건 ②의 답이다. 차단 번들이면 `차단: 예` · `export_allowed: False`가 한 화면에 같이 보인다.

### 2.2 ④ 구간 — 재현 지문 3종을 어디서 보여주는가

**`summary.md` 하나로는 3종이 다 안 나온다.** `approval_hash`는 summary에 없다.

| 지문 | 위치 | 근거 |
| --- | --- | --- |
| `config_hash` | `summary.md` §주요 해시 | `scripts/make_evidence_bundle.py:392` (build_summary_md) |
| `computation_hash` | `summary.md` §주요 해시 | 〃 |
| `approval_hash` | **`replay_diff.json`의 `hashes.approval_hash`** | `scripts/make_evidence_bundle.py:316` (build_replay_diff) |

- `summary.md`의 `report_hash`는 재현 지문 3종이 **아니다.** 번들이 `report` 전문에서 계산한 값이며 화면에도 그렇게 적혀 있다.
- 왜 3종뿐인지 묻거든 `docs/reproducibility_scope.md` §3 「제외 대상에 대한 원칙」을 연다.
- `replay_diff.json`을 열면 `status: single_run_only`가 같이 보인다. **먼저 말한다** — 이 번들은 1회 실행분 해시만 싣고 있고 2회 대조는 3분 재실행에서 지금 한다고 예고한다.

### 2.3 ⑥ 구간 — `available:false`가 나왔을 때 말할 것

표기 규약의 원천은 `app/evidence/schema.py:53` (unavailable)이고, `summary.md`에서는 `없음 (사유)` 형태로 풀려 나온다.

| 화면에 뜨는 것 | 말할 것 | 근거 |
| --- | --- | --- |
| `llm_audit.raw_prompt_and_response` | 결함이 아니라 "비밀값 없이 기록"이라는 설계. 복원 경로는 `manifest.generated_by.git_sha` | `docs/evidence_bundle_schema.md` §8.1 |
| `trace.node_execution_order` | state에 없는 값이라 채울 수 없음. CLI 출력에만 존재 | 〃 §8.2 |
| `hard_stop_record.manual_review_gate` (성공 번들) | 공백이 아니라 **정상**. 같은 파일 `blocked=false`가 같은 사실을 파생값으로 확인 | 〃 §8.5 |
| `LangSmith trace URL: 없음 (...)` | 추적이 꺼진 실행. `trace_id`는 그대로 있고 원본 키 경로가 사유에 적혀 있음 | 〃 §5 |

핵심 문장은 하나다 — **"없는 것"과 "안 채운 것"을 구분해 사유와 원본 키 경로를 함께 적었다** (`docs/evidence_bundle_schema.md` §5).

### 2.4 ⑤ 구간 — 지목 번들별 심화 파일

- **성공 번들** → `judge_rationale.json`: `rubric` 6축의 `passed`/`reason`, 각 축에 `axis_en`·`axis_ko` 병기. 6축 명칭 SSOT는 `app/judge/axes.py`.
- **차단 번들** → `hard_stop_record.json`: `blocked`·`blocked_derived_from`·`manual_review_gate` 12키·`source_paths`.
  `decision_hash`는 `app/nodes/manual_review_gate.py:147` (decision_hash)가 만든다. 계약 표는 `docs/hard_stop_contract.md` §6.
- 어느 쪽이든 `source_paths`를 짚어 **번들의 모든 값이 원본 state 키로 되짚힌다**는 것을 보인다.

---

## 3. 3분 재실행 동선 (합 180초)

### 3.1 실행 명령

```bash
python scripts/run_graph.py --auto-approve --offline \
  --dump-state replay.json --evidence-bundle
```

번들은 그래프 종료 직후 in-process로 생성된다 — `scripts/run_graph.py:66` (generate_evidence_bundle). 사람이 파일을 옮기는 단계는 없다.

### 3.2 구간 배분

| # | 구간 | 목표 | 내용 |
| --- | --- | --- | --- |
| ① | 먼저 밝히기 | 20초 | §4의 4줄을 **명령을 치기 전에** 말한다 |
| ② | 실행 | 40초 | 위 명령 1줄. 진행 중 노드 실행 순서가 화면에 흐른다 |
| ③ | 지문 3종 대조 | 40초 | 새 번들의 `summary.md`·`replay_diff.json` vs 제출본 |
| ④ | §2 항목 대조 | 40초 | 아래 명령 출력 |
| ⑤ | 제외 대상 공개 | 30초 | 같은 출력의 `citations` 줄이 `False`인 것을 그대로 보인다 |
| — | 예비 | 10초 | — |

**합계 180초.**

### 3.3 각 경로의 소요 (실측 인용)

| 경로 | 명령 | 실측 | 출처 |
| --- | --- | --- | --- |
| 성공 (오프라인) | `--auto-approve --offline` | 평균 36.8초 (34.8~39.8, N=10) | `reproducibility_scope.md` §4.1 모드 A |
| 성공 (실 Azure) | `--auto-approve` | 평균 32.4초 (31.6~33.7, N=10) | 〃 모드 B |
| 차단 | `--auto-approve --offline --force-judge-fail` (상한 이상) | 평균 116.0초 (74.2~131.4, N=5) | 〃 모드 C |

**라이브 재실행은 성공 경로로 한다.** 차단 경로는 최대 131.4초로 3분에서 남는 여유가 48.6초뿐이며,
그 여유로는 대조 구간 ③④⑤(110초)를 소화할 수 없다 (`reproducibility_scope.md` §7 주의사항 2).

### 3.4 대조에서 무엇을 보여주는가

두 덤프를 §2 항목만 골라 비교한다. `canonical_for_replay`는 `trace_id`만 걷어내므로
(`app/evidence/state_dump.py:159` (canonical_for_replay)) **전체 비교는 실패하는 것이 정상**이다 — 쓰지 않는다.

```bash
python -c "
import json
a, b = [json.load(open(p)) for p in ('submitted.json', 'replay.json')]
ra, rb = [(x.get('report') or {}).get('reproducibility') or {} for x in (a, b)]
for k in ('config_hash', 'computation_hash', 'approval_hash'):
    print(f'{k}: {ra.get(k) == rb.get(k)}')
for k in ('metrics', 'explanations'):
    print(f'{k}: {a.get(k) == b.get(k)}')
print('citations (재현 대상 아님, 참고):', a.get('citations') == b.get('citations'))
"
```

- 위 5줄은 §2 무조건 보장 대상이다. 여기서 `False`가 나오면 범위를 조정하지 말고 원인을 찾는다 (`reproducibility_scope.md` §9).
- 마지막 줄은 §3 제외 대상이다. **`False`로 나오는 것이 정상이며, 그 사실을 감사자보다 먼저 말한다.**
- `prompt_hash.rag_cite`도 §2 대상이며 새 번들의 `llm_audit.json` `prompt_hash`에서 확인한다.

---

## 4. 먼저 밝힐 것 — 감사자가 화면에서 발견하기 전에 말한다

재실행 명령을 치기 **전에** 4줄로 말한다. 근거는 전부 `docs/reproducibility_scope.md` §3이다.

- [ ] **인용 집합·순서는 재현 대상이 아니다.** retriever는 결정론이지만 인용 선택 단계가 LLM이라 갈린다 (§3, §5).
- [ ] **`judge.rubric.*.reason` 문구는 재현 대상이 아니다.** LLM 산문이다. 판정(pass/fail)도 §2.1의 반복 실측 대상이지 무조건 보장이 아니다.
- [ ] **`prompt_hash.judge_eval`은 재현 대상이 아니다.** judge 프롬프트 payload에 `citations`가 들어가 위 항목의 파생으로 움직인다.
- [ ] **IPS 추출 산출물(`ips.Unique` 및 파생 5개 경로)은 재현 대상이 아니다.** Azure 응답 비결정성이며, §4.1 모드 B에서 실제로 갈렸다. `--offline`은 고정 프로필을 돌려주므로 이번 데모에서는 갈리지 않는다 (§6).

추가로 한 줄 더 말한다 — **`--offline`은 재현성 모드가 아니다.** 시장 데이터와 IPS 추출만 스텁으로 바꾸고
RAG 검색·rag_cite·judge_eval은 실제 Azure를 호출한다 (`reproducibility_scope.md` §6).
"offline이라 재현된다"고 말하지 않는다. 재현되는 이유는 §2 항목이 결정론 계층 산출물이기 때문이다.

---

## 5. 예상 질문과 답변 근거 위치

**답변 문안은 쓰지 않는다.** 아래는 "어느 문서 어느 절을 여는가"만 적는다.

| # | 겨냥 | 질문 | 근거 위치 |
| --- | --- | --- | --- |
| 1 | 무효 조건 ① | judge가 정답 라벨을 미리 본 것 아닌가 | `goldenset/judge_inputs/README.md` · `tests/test_goldenset_judge_inputs.py` · `docs/symphony_proof_plan.md` §2 R1 |
| 2 | 무효 조건 ② | 실패했는데 확정된 리포트가 있나 | `docs/hard_stop_contract.md` §6 · 번들 `hard_stop_record.json` · `app/nodes/assemble_report.py:369` (report_is_exportable) |
| 3 | 무효 조건 ③ | 같은 입력인데 결과가 다르면 | `docs/reproducibility_scope.md` §2 · §2.1 · §3 |
| 4 | 무효 조건 ④ | 이 서류철을 사람이 조립한 것 아닌가 | `docs/evidence_bundle_schema.md` §1 · §2 · `manifest.generated_by` |
| 5 | 평가 포인트 1 | 라벨 기준이 사람마다 다르지 않나 | `goldenset/labeling-guide.md` · `goldenset/reports/agreement_before.md` |
| 6 | 평가 포인트 2 | judge가 틀린 건 몇 건이고 원인은 무엇인가 | `docs/symphony_proof_plan.md` §2 R2 · `docs/evidence_bundle_schema.md` §4.8 · §8.4 |
| 7 | 평가 포인트 3 | 문서에 적은 규칙이 코드에 그대로 있나 | `docs/hard_stop_contract.md` §7 · `tests/test_docs_config_consistency.py` · `tests/test_docs_line_anchors.py` |
| 8 | 평가 포인트 4 | 명령 몇 번으로 만들어지나 | `docs/evidence_bundle_schema.md` §2 · `scripts/run_graph.py:66` (generate_evidence_bundle) |
| 9 | 평가 포인트 5 | 재현 지문이 왜 3개뿐인가 | `docs/reproducibility_scope.md` §3 「제외 대상에 대한 원칙」 |
| 10 | 인용 검증 | 인용문이 실제 원문에 있는지 어떻게 아나 | `docs/hard_stop_contract.md` §5 · 번들 `citation_verification.json` |
| 11 | 재시도 상한 | 재시도는 몇 번까지이고 그 숫자는 어디 있나 | `docs/hard_stop_contract.md` §2 · `app/nodes/judge_eval.py:28` (resolve_max_judge_retries) |
| 12 | 차단 지문 | 차단 지문이 실행마다 같나 | `docs/reproducibility_scope.md` §4.2 · `docs/evidence_bundle_schema.md` §4.5 |
| 13 | 누락 표기 | "없음"으로 나온 칸은 안 채운 것 아닌가 | `docs/evidence_bundle_schema.md` §5 · §8 |
| 14 | 탈락 인용 | 떨어진 인용은 어디에 남나 | `docs/hard_stop_contract.md` §5 「탈락 인용 기록」 · `scripts/make_evidence_bundle.py:241` (_rejected_citations) |

---

## 6. 실패 대비

### 6.1 재실행이 3분을 넘길 때

- **판단 시점**: 실행 시작 후 60초. 성공 경로 실측 상한이 39.8초(§4.1 모드 A)이므로 이 시점에 안 끝나면 정상 범위 밖이다.
- **대응**: 실행을 끊지 않고 두되, 대조 구간을 **지문 3종 1개 구간(40초)으로 줄인다.** §2 항목 대조와 제외 대상 공개는 버린다.
- **그래도 안 끝나면**: 재실행을 중단하고 §6.2의 오프라인 무결성 검증으로 전환한다. 중단했다는 사실을 먼저 말한다.

### 6.2 네트워크가 끊길 때

`--offline`이어도 RAG·judge는 Azure를 호출하므로(§4의 마지막 줄) **재실행 자체가 성립하지 않는다.**
네트워크가 없으면 인용이 0건이 되고, `config/config.yaml`의 `strict_citation_gate`가 켜져 있으면 출처 축이 실패해
정상적으로 차단된다 — `app/judge/rubric.py:63` (source_validity). 결함이 아니라 설계된 fail-closed 동작이지만,
**재현 데모로는 쓸 수 없다.**

대응 — 제출된 번들 파일만으로 되는 **오프라인 무결성 검증 2종**으로 전환한다. 둘 다 네트워크가 필요 없다.

```bash
# ① bundle_hash 재계산 — manifest.json의 sha256과 bundle_hash.txt가 같아야 한다
sha256sum manifest.json | cut -d' ' -f1
cat bundle_hash.txt

# ② manifest.files 전건 재계산 — 7종 파일 해시가 manifest 기록과 같아야 한다
python -c "
import json, hashlib, pathlib
m = json.load(open('manifest.json'))
bad = [f for f, h in m['files'].items()
       if hashlib.sha256(pathlib.Path(f).read_bytes()).hexdigest() != h]
print('불일치:', bad or '없음', f\"({len(m['files'])}건 검사)\")
"
```

말할 것 — **bundle_hash 하나가 번들 전체를 봉인한다.** 어느 파일이든 1바이트가 바뀌면 그 파일 해시가 바뀌고,
manifest가 바뀌고, bundle_hash가 바뀐다 (`docs/evidence_bundle_schema.md` §6).

### 6.3 차단 번들이 지정될 때

- **5분 구간은 그대로 간다.** ⑤ 구간만 `hard_stop_record.json`으로 바꾼다(§2.4). 차단 번들도 파일 9종이 전부 있다.
- **3분 재실행은 성공 경로 명령으로 한다.** 차단 경로 재실행이 3분에 안 들어가는 근거(§3.3)를 **먼저 밝히고** 시작한다.
- 차단의 재현은 라이브 실행 대신 `decision_hash`로 갈음한다 — `reproducibility_scope.md` §4.2가 추적 off/on 4회 실행 전부 동일함을 기록하고 있고, `trace_id`가 갈린 실행 쌍에서도 같았다.
- UI까지 묻거든 `ui/report_export.py:18` (pdf_export_state)를 연다. `report_is_exportable`이 `false`면 PDF 저장 버튼이 비활성이고, 안내문이 "Judge 미통과 또는 수동검토 대기"로 분기한다.

---

## 7. 미검증 항목 — 이 문서가 측정하지 않은 것

- **구간별 목표 시간(30초·60초 등)은 배분안이지 실측이 아니다.** 총합만 300초·180초에 맞춰 놓았다.
  구간 실측은 리허설에서 타이머로 해야 하며, 이는 `docs/symphony_proof_plan.md` §2 R5 DoD의 미체크 항목이다.
- **§3.3 표의 초 단위 수치는 전부 인용이다.** 이 문서 작성 시 그래프를 실행하지 않았다.
  출처는 `reproducibility_scope.md` §4.1(모드 A·B·C)과 §7이며, 측정 기준 커밋은 그 문서 머리말이 밝힌다.
- **`scripts/replay_verify.py`는 레포에 없다.** `docs/symphony_proof_plan.md` §2 R5가 산출물로 예고했으나 미구현이라,
  §3.4의 대조는 자동 스크립트가 아닌 수동 명령이다. 리허설 전에 스크립트로 굳히면 대조 구간이 짧아진다.
- **번들 3건의 실물이 아직 없다.** 현재 레포에 `evidence/` 디렉터리가 없다. 8/6 제출 전에 3건을 만들고,
  만든 뒤 이 런북의 파일명·키 이름을 실물로 한 번 더 대조한다.
- **§6.2의 검증 명령 2종은 합성 state로 만든 번들에서만 확인했다.** 그래프를 돌리지 않고
  `make_bundle`(직렬화기)만 호출해 만든 번들이라 파일 구조·해시 체계는 같지만, 실행 산출물이 들어간
  실물 번들에서 다시 한 번 돌려 봐야 한다.
- **`evidence/`는 `.gitignore`에 없다.** 번들을 만든 뒤 실수로 커밋되지 않도록 제출 전에 확인한다.
- **calibration 요약은 번들 파일로 나가지 않는다.** 계약은 `app/evidence/schema.py`에 있으나
  `BUNDLE_FILENAMES`에 없다 (`docs/evidence_bundle_schema.md` §8.4). 질문 6번이 나오면 이 사실을 먼저 밝힌다.
- **UI 시연 동선은 이 문서 범위 밖이다.** §6.3의 `pdf_export_state` 1건만 확인했고,
  Streamlit 화면의 다른 위치는 확인하지 않았다.
