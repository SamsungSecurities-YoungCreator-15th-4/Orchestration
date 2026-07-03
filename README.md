# Orchestration
재현가능·설명가능 리스크 리포트 엔진 (LangGraph 스켈레톤)

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
