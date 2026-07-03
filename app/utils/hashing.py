"""결정론적 해시 유틸리티 (재현성 검증용)."""
import hashlib
import json


def sha256_of_dict(d: dict) -> str:
    """dict를 키 정렬된 canonical JSON으로 직렬화해 sha256 해시를 반환."""
    canonical = json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256_of_file(path: str) -> str:
    """파일 내용의 sha256 해시를 반환."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
