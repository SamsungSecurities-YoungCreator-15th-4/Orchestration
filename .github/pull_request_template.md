## 작업 내용 요약

<!-- 한두 줄로 이 PR이 무엇을 하는지 적습니다. -->

## 관련 이슈 / 노션 링크

<!-- 예: closes #12, https://www.notion.so/... -->

## 변경 사항 (무엇을, 왜)

- **무엇을**:
- **왜**:

## 테스트 여부

<!-- 로컬에서 어떻게 동작 확인했는지 구체적으로 적습니다. -->

- [ ] 그래프 실행: `python scripts/run_graph.py --auto-approve` 완주 확인
- [ ] 자동 테스트: `pytest` 통과
- [ ] (해당 시) 분기/루프 시연: `--with-conflict`, `--force-judge-fail N` 확인
- [ ] (해당 시) 재현성: 동일 명령 2회 실행 시 computation_hash 동일

## 리뷰어가 봐줬으면 하는 부분

<!-- 특별히 의견이 필요한 부분, 고민했던 트레이드오프 등 -->

## 체크리스트

- [ ] 빌드/실행이 로컬에서 통과한다
- [ ] `.env` · API 키 · 비밀번호 등 비밀 정보를 포함하지 않는다
- [ ] 커밋 메시지가 `타입: 설명` 컨벤션을 따른다 (feat / fix / docs / chore / refactor / test)
- [ ] `app/engine/` 결정론 계층에 langchain/llm import를 추가하지 않았다
- [ ] `app/state.py`(팀 데이터 계약)를 임의 수정하지 않았다
- [ ] (해당 시) 관련 문서(README, AGENTS.md 등) 업데이트
