"""Streamlit Cloud 환경의 ``ui`` 패키지 충돌 방지 테스트."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_ui_package_wins_over_external_package(tmp_path):
    """동명 외부 패키지가 있어도 프로젝트의 ui.rag_evidence를 가져온다."""

    external_root = tmp_path / "external"
    external_ui = external_root / "ui"
    external_ui.mkdir(parents=True)
    (external_ui / "__init__.py").write_text(
        'raise RuntimeError("external ui package imported")\n',
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(external_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import ui; "
                "from ui.rag_evidence import RAG_EVIDENCE_SECTIONS; "
                "print(ui.__file__); "
                "assert len(RAG_EVIDENCE_SECTIONS) == 4"
            ),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == (ROOT / "ui" / "__init__.py").resolve()
