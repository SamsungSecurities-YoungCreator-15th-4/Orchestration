"""근거(RAG 인용) 표에서 출처 파일명을 원문 문서 링크로 연결하기 위한 매핑.

corpus/manifest.md의 21개 문서 + methodology 2건, 총 23건의 원문 위치(팀 내부
공유 드라이브)를 담는다. 원문 PDF 자체는 저작권상 git에 커밋하지 않으므로,
여기 있는 것은 파일명 -> 외부 링크 매핑일 뿐 원문이 아니다.
"""

from __future__ import annotations

DOCUMENT_LINKS: dict[str, str] = {
    "methodology_stress_2026.pdf": "https://drive.google.com/file/d/10kA_DGMkF7oHF0MMx1i6cp88M2ClcdBF/view?usp=drive_link",
    "methodology_var_cvar_2026.pdf": "https://drive.google.com/file/d/1P18o8teVQxAgMow4mf7sSIW-AuB8C2-w/view?usp=drive_link",
    # house_view
    "samsung_bond_202408.pdf": "https://drive.google.com/file/d/1wA7DchiC_Z8sbweW-jYOKFmJGbWsqZIF/view?usp=drive_link",
    "samsung_bond_202409_check.pdf": "https://drive.google.com/file/d/1Hr9_dlZgz4oE8H9JtaFmWGKrvWxYzoen/view?usp=drive_link",
    "samsung_bond_202409_outlook.pdf": "https://drive.google.com/file/d/1XnmaukMW3QvSA3Qw4fXhxKPzR-EVX8LW/view?usp=drive_link",
    "samsung_bond_202502.pdf": "https://drive.google.com/file/d/1BcLdtph0nDco6Ni6f4CmW7i7C7-_zvpK/view?usp=drive_link",
    "samsung_equity_202510.pdf": "https://drive.google.com/file/d/1Vq9-Fhrs41SNiMG_-_zdylTNPfmLsMxp/view?usp=drive_link",
    "samsung_equity_202511.pdf": "https://drive.google.com/file/d/1Bk8cUFuCUWHxJAWIzRXyT5ot8As-HqE7/view?usp=drive_link",
    # macro
    "bok_framework_2026.pdf": "https://drive.google.com/file/d/1lDbSUGPUqXVlabsgAxqSsMMAfMvbrS25/view?usp=drive_link",
    "bok_mpd_202601.pdf": "https://drive.google.com/file/d/1GL2KFnAcaQc4ej1G6ZUonqmHnGQKkGDh/view?usp=drive_link",
    "bok_mpd_202602.pdf": "https://drive.google.com/file/d/1izSJlSbwBjCEXj-t91y0r3QjQYmfg86V/view?usp=drive_link",
    "bok_mpd_202604.pdf": "https://drive.google.com/file/d/1Stlx8uf8LGtp-kra2hCDP7QrAHdLtHob/view?usp=drive_link",
    "bok_mpd_202605.pdf": "https://drive.google.com/file/d/1p6gnsakJOw_xLMyzvPLAcNSICbWcKEAB/view?usp=drive_link",
    "fed_fomc_202601.pdf": "https://drive.google.com/file/d/1rsxMXDcRJMPIzvNfV4VO_3K87rHLzYtY/view?usp=drive_link",
    "fed_fomc_202604.pdf": "https://drive.google.com/file/d/1Hv6FvyASKsJGvtLSSwHJSEXUgLe0wZ6J/view?usp=drive_link",
    # tax
    "nts_building_2026.pdf": "https://drive.google.com/file/d/1kWXRZP4imDBlcDY992TmiFQXrniRPXz8/view?usp=drive_link",
    "nts_inherit_2026.pdf": "https://drive.google.com/file/d/1wqJsR2s-Iic4Xl9sBKi0ScmHGWBEeC3o/view?usp=drive_link",
    "nts_sme_2026.pdf": "https://drive.google.com/file/d/1vImDqjtTRb1CM_aetuwA8xY-GFa-aZMI/view?usp=drive_link",
    "nts_taxguide_2026_vol1.pdf": "https://drive.google.com/file/d/1TXmWB3c_z9JLsojkfyywTTIqtYZFnRKY/view?usp=drive_link",
    "nts_taxguide_2026_vol2_errata.pdf": "https://drive.google.com/file/d/12dZRCtqXr2FK57MALfxAyM9kgKBMwCyZ/view?usp=drive_link",
    "nts_taxguide_2026_vol2.pdf": "https://drive.google.com/file/d/1Oa86Ywi9qFW52RDMf7-Q7RDXFJylm-hT/view?usp=drive_link",
}


def document_url(source: str) -> str | None:
    """출처 파일명(예: bok_framework_2026.pdf)에 대응하는 원문 링크를 찾는다."""
    if not isinstance(source, str):
        return None
    return DOCUMENT_LINKS.get(source.strip())
