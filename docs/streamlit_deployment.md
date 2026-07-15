# Streamlit Community Cloud 릴리스·배포

## 배포 좌표

- Repository: `SamsungSecurities-YoungCreator-15th-4/Orchestration`
- Branch: `main`
- Entrypoint: `ui/app.py`
- Python: `3.10` (로컬·CI 검증 버전과 일치)

Community Cloud는 저장소 루트에서 앱을 실행하므로, 루트의 `requirements.txt`와
`ui/app.py` 조합을 사용한다. Python 버전은 최초 배포의 **Advanced settings**에서
선택하며, 배포 후에는 앱을 삭제·재배포해야 바꿀 수 있으므로 3.10으로 고정한다.

## 1. RAG 인덱스 공급 준비

검증된 로컬 `data/chroma/`와 21개 PDF가 준비된 승인 환경에서 실행한다.

```bash
python scripts/package_rag_index.py --index-version YYYY-MM-DD.vN
```

생성된 ZIP과 sidecar manifest를 private Azure Blob container에 업로드하고, 두
blob에 읽기(`r`) 전용·단기 만료 SAS URL을 각각 발급한다. 목록·쓰기·삭제 권한은
부여하지 않는다. 원본 PDF는 업로드하지 않는다. 자세한 무결성 계약은
[`rag_index_deployment.md`](rag_index_deployment.md)를 따른다.

## 2. Streamlit Secrets

`.streamlit/secrets.toml.example`을 기준으로 **루트 수준** TOML 값을 App settings의
Secrets에 입력한다. 루트 수준 값은 `st.secrets`뿐 아니라 환경변수로도 제공되므로,
RAG 공급 코드와 Azure OpenAI·LangSmith 코드가 동일한 설정을 사용할 수 있다.

민감정보:

- `AZURE_OPENAI_API_KEY`
- `LANGSMITH_API_KEY`
- `RAG_INDEX_BLOB_URL`, `RAG_INDEX_MANIFEST_URL`의 SAS query string

운영 필수값:

- Azure OpenAI endpoint, API version, gpt-4o deployment, embedding deployment
- RAG ZIP·manifest SAS URL, version, 패키징 시 출력된 SHA-256
- `RAG_INDEX_REQUIRED = true`
- LangSmith APAC endpoint, project, API key
- `LANGSMITH_TRACING = true`
- `LANGSMITH_HIDE_INPUTS = true`, `LANGSMITH_HIDE_OUTPUTS = true`

`.env`의 Azure OpenAI·LangSmith 값(API version 포함)은 대응하는 TOML 키에 옮길 수 있지만,
`.env` 형식을 그대로 붙여 넣으면 안 된다. Blob 관련 4개 값은 패키징·업로드 후
별도로 채워야 한다. 실제 `secrets.toml`은 `.gitignore` 대상이며 커밋하지 않는다.

## 3. 배포 및 확인

1. `main` 대상 릴리스 PR의 CI·CodeQL·필수 리뷰를 확인하고 병합한다.
2. Streamlit Community Cloud에서 위 저장소·브랜치·엔트리포인트를 선택한다.
3. Advanced settings에서 Python 3.10과 Secrets를 저장한 뒤 배포한다.
4. 시작 화면이 RAG 오류 없이 열리는지 확인한다.
5. 세무 이슈가 포함된 시나리오로 분석해 4개 category의 verified citation,
   Judge 통과, HITL `locked`, LangSmith input·analysis trace를 확인한다.
6. 로컬 승인 환경에서는 다음 명령도 모두 통과해야 한다.

```bash
python scripts/preflight_release.py --real
```

SAS 또는 API 키를 변경한 경우 App settings에서 Secrets를 갱신하고 앱을 reboot한다.
배포 이후 `main` 변경은 Community Cloud에 자동 반영되며, 의존성 변경은 재설치 후
재배포된다.
