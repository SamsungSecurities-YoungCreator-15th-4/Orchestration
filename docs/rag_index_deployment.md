# Streamlit 배포용 RAG 인덱스 공급

원본 PDF와 Chroma 인덱스는 Git에 커밋하지 않는다. 승인된 환경에서 만든 Chroma를
ZIP으로 패키징하고, sidecar manifest와 함께 private Azure Blob container에서
read-only SAS URL로 공급한다.

## 1. 아티팩트 생성

로컬 `corpus/`에 계약된 PDF 21건과 최신 `data/chroma/`가 있는 상태에서 실행한다.

```bash
python scripts/package_rag_index.py --index-version <YYYY-MM-DD.vN>
```

`data/rag-index-artifacts/`에 다음 두 파일이 생성된다.

- `rag-index-<YYYY-MM-DD.vN>.zip`: PDF를 포함하지 않은 Chroma 파일
- `rag-index-<YYYY-MM-DD.vN>.manifest.json`: 생성시각, 임베딩 모델, 21개 source의
  PDF SHA-256, category별 source/chunk 수, ZIP SHA-256

CLI가 출력한 ZIP SHA-256은 Streamlit secret에 별도로 고정한다. manifest와 ZIP을
같이 변조해도 이 고정값과 다르면 설치되지 않는다.

## 2. Azure Blob 업로드

1. Azure Storage Account에 private container를 만든다.
2. 위 ZIP과 manifest JSON만 업로드한다. `corpus/**/*.pdf`는 업로드하지 않는다.
3. 각 blob에 읽기(`r`) 전용이고 만료시간이 제한된 SAS URL을 발급한다.
4. SAS URL에 쓰기·삭제·목록 권한을 부여하지 않는다.

Azure Portal로 진행할 수 있어 별도 Python 패키지는 필요하지 않다. Azure CLI를
사용하는 경우에도 업로드 명령과 계정 정보는 저장소 문서나 셸 기록에 남기지 않는다.

## 3. Streamlit secrets 설정

배포 앱의 Secrets에 다음 값을 등록한다. URL과 SHA-256은 예시값을 쓰지 말고 패키징
결과를 사용한다.

```toml
RAG_INDEX_BLOB_URL = "https://<account>.blob.core.windows.net/<container>/<artifact>.zip?<sas>"
RAG_INDEX_MANIFEST_URL = "https://<account>.blob.core.windows.net/<container>/<manifest>.json?<sas>"
RAG_INDEX_VERSION = "<YYYY-MM-DD.vN>"
RAG_INDEX_SHA256 = "<package_rag_index.py가 출력한 64자리 SHA-256>"
RAG_INDEX_REQUIRED = "true"
```

앱 시작 시 다음 순서로 검증한다.

1. 설치된 동일 버전·SHA-256 인덱스가 있으면 Chroma metadata를 재검증해 재사용
2. sidecar manifest의 version, embedding model, collection, 21개 source와 4개
   category 계약 검증
3. ZIP 다운로드 후 크기·SHA-256 검증
4. 안전한 경로만 임시 디렉터리에 압축 해제
5. 실제 Chroma의 전체/category별 chunk 수와 source 목록을 manifest에 대조
6. 모든 검증을 통과한 경우에만 `data/chroma/`로 원자적 교체

어느 단계에서든 실패하면 SAS URL이나 내부 예외를 화면에 노출하지 않고 Streamlit에
운영 오류를 표시한 뒤 `st.stop()`으로 분석을 중단한다.

## 4. 로컬 개발

remote 설정이 없고 `data/chroma/`가 존재하면 기존 로컬 인덱스를 사용한다. 배포
환경에서는 `RAG_INDEX_REQUIRED=true`로 설정해 remote 값 누락도 즉시 실패시키는
것을 권장한다.

## 5. 배포 아티팩트 이력

SAS URL과 원본 PDF, Chroma 파일은 저장소에 기록하지 않는다. 아래에는 배포 시
Streamlit secret과 대조할 수 있는 비민감 메타데이터만 남긴다.

### 2026-07-16.v3

- 상태: Blob 업로드 및 Streamlit 반영 전 배포 후보
- 임베딩 모델: `text-embedding-3-small`
- source: 21건
- chunk: 1,393건
- ZIP SHA-256: `0192506700fe7a3559efa9235bc4c23c6b67b85da075802ad3af095f7c0cbc3c`
- 변경 문서: `methodology_stress_2026.pdf`
  - v2 PDF SHA-256: `1687bac2c99948d2fcd8fdc661d1db68d0649e4e3311dc761811ca57c9423ff0`
  - v3 PDF SHA-256: `d0710c9a36c94553ce70f832df4abc87c6fcdc10436c807f7f74792251ff9213`
- 유지 문서: `methodology_var_cvar_2026.pdf`
  - PDF SHA-256: `57f2aecad9c7e6159e00083979f3f2398edc0ef303de64aa61331b8db2826a05`
- 검증 결과: ZIP 무결성, 21개 source, 4개 category, 1,393개 chunk 계약 통과
