"""배포용 Chroma ZIP과 sidecar manifest 생성 CLI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag.deployment import create_index_artifact  # noqa: E402
from app.rag.ingest import DEFAULT_CORPUS_DIR, DEFAULT_PERSIST_DIR  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Azure Blob 배포용 RAG 인덱스 패키징")
    parser.add_argument("--index-version", required=True)
    parser.add_argument("--persist-dir", default=DEFAULT_PERSIST_DIR)
    parser.add_argument("--corpus-dir", default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--output-dir", default="data/rag-index-artifacts")
    args = parser.parse_args()

    archive_path, manifest_path, manifest = create_index_artifact(
        index_version=args.index_version,
        persist_dir=args.persist_dir,
        corpus_dir=args.corpus_dir,
        output_dir=args.output_dir,
    )
    print(f"artifact={archive_path}")
    print(f"manifest={manifest_path}")
    print(f"sha256={manifest['artifact']['sha256']}")
    print(
        f"sources={manifest['source_count']} chunks={manifest['chunk_count']} "
        f"version={manifest['index_version']}"
    )


if __name__ == "__main__":
    main()
