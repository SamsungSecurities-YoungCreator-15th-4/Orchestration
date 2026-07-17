"""자연어 IPS·포트폴리오 입력부터 PB 승인·리스크 결과까지 제공하는 Streamlit UI."""
import base64
import html
import re
import sys
import uuid
from datetime import date
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph import build_graph
from app.nodes.load_inputs import (
    ASSET_DEFINITIONS,
    DUMMY_PORTFOLIO,
    SAMPLE_RAW_INPUT,
    TOTAL_ASSET_KRW,
    portfolio_from_percentages,
)
from app.observability.langsmith import (
    merge_observability,
    prepare_trace_invocation,
    tracing_scope,
)
from app.state import (
    FIXED_AGE,
    FIXED_ASSET_EOK,
    FIXED_GOAL,
    FIXED_JOB,
    FIXED_RISK,
    IPSProfile,
)
from ui.document_links import document_url
from ui.index_supply import prepare_index_or_stop
from ui.pb_approvers import approver_label, validate_pb_approver
from ui.rag_evidence import (
    RAG_EVIDENCE_SECTIONS,
    citation_table_rows,
    group_verified_citations,
)

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="S.ymphony", layout="wide")
prepare_index_or_stop(st)

LOGO_B64 = base64.b64encode((Path(__file__).parent / "assets" / "symphony-logo.png").read_bytes()).decode()

st.markdown(
    """
    <style>
    html, body, [class*="css"] { font-size: 15px; }
    [data-testid="stMetricValue"] { font-size: 1.3rem; }
    [data-testid="stMetricLabel"] { font-size: 0.85rem; }
    table { font-size: 0.9rem; }

    .app-topbar {
        background: #FFFFFF; border: 1px solid #E4EAF2; border-radius: 16px;
        box-shadow: 0 1px 2px rgba(15,23,42,0.04);
        padding: 12px 22px; display: flex; align-items: center; gap: 18px;
        margin-bottom: 16px;
    }
    .app-topbar img { height: 32px; display: block; }
    .app-topbar .divider { width: 1px; height: 30px; background: #E4EAF2; }
    .app-topbar .title { font-size: 15.5px; font-weight: 800; letter-spacing: -0.01em; color: #0F172A; }

    .report-header {
        background: #FFFFFF; border: 1px solid #E4EAF2; border-radius: 16px;
        box-shadow: 0 1px 2px rgba(15,23,42,0.04);
        padding: 18px 24px; display: flex; align-items: center; gap: 40px;
        margin-bottom: 1rem; flex-wrap: wrap;
    }
    .report-header .titles { display: flex; flex-direction: column; gap: 6px; flex: 1 1 320px; min-width: 0; }
    .report-header h1 { color: #0F172A; margin: 0; font-size: 22px; font-weight: 800; letter-spacing: -0.01em; }
    .report-header p { color: #64748B; margin: 0; font-size: 13px; }

    .step-indicator {
        flex: 2 1 420px; display: flex; align-items: center; flex-wrap: wrap;
        row-gap: 10px; min-width: 0;
    }
    .step-indicator .step { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
    .step-indicator .num {
        width: 28px; height: 28px; border-radius: 999px; display: flex;
        align-items: center; justify-content: center; font-size: 12.5px;
        font-weight: 800; flex-shrink: 0;
    }
    .step-indicator .num-done { background: #2563EB; color: #FFFFFF; }
    .step-indicator .num-pending { background: #EFF6FF; color: #2563EB; border: 1px solid #BFDBFE; }
    .step-indicator .label { font-size: 13px; font-weight: 700; color: #0F172A; white-space: nowrap; }
    .step-indicator .line { flex: 1 1 20px; min-width: 12px; height: 2px; background: #E4EAF2; margin: 0 12px; border-radius: 1px; }

    .fixed-field-card {
        border: 1px solid #EDF1F7; border-radius: 10px; background: #F8FAFC;
        padding: 10px 14px; display: flex; flex-direction: column; gap: 3px;
    }
    .fixed-field-card .fixed-label { font-size: 11px; font-weight: 600; color: #94A3B8; letter-spacing: 0.02em; }
    .fixed-field-card .fixed-value {
        font-size: 13.5px; font-weight: 700; color: #475569;
        overflow-wrap: break-word; line-height: 1.4;
    }

    /* 입력 위젯을 배경과 구분되게 — 연회색 배경 + 테두리 (목업 참고) */
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea {
        background: #F8FAFC !important;
        border: 1px solid #D7DFEC !important;
        border-radius: 10px !important;
        padding: 10px 14px !important;
    }
    [data-testid="stTextInput"] input:focus,
    [data-testid="stTextArea"] textarea:focus {
        border-color: #2563EB !important;
        box-shadow: 0 0 0 2px rgba(37,99,235,0.12) !important;
        background: #FFFFFF !important;
    }
    [data-testid="stTextInput"] div[data-baseweb="input"],
    [data-testid="stTextArea"] div[data-baseweb="textarea"] {
        border: none !important; background: transparent !important;
    }

    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .asset-pct-marker) {
        border: 1px solid #E4EAF2; border-radius: 14px; padding: 14px 16px 12px;
        margin-bottom: 10px; gap: 4px; position: relative;
    }
    /* −/+ 스테퍼 묶음을 카드 우상단(자산명 행)으로 올린다.
       absolute의 containing block은 position:relative인 카드라서
       overflow:hidden인 입력 컨테이너에 잘리지 않는다. */
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .asset-pct-marker)
        > div[data-testid="stElementContainer"]:has([data-testid="stNumberInput"]) {
        position: static;
    }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .asset-pct-marker)
        [data-testid="stNumberInputContainer"] > div:last-child {
        position: absolute; top: 14px; right: 16px; gap: 10px;
    }
    /* 숫자 바로 뒤에 % 단위 표기 */
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .asset-pct-marker)
        [data-testid="stNumberInputContainer"]::after {
        content: "%"; align-self: center;
        font-size: 15px; font-weight: 800; color: #2563EB; margin-left: 4px;
    }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .asset-pct-marker)
        [data-testid="stNumberInputContainer"] {
        border: none; background: transparent; justify-content: flex-start; gap: 0;
    }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .asset-pct-marker)
        [data-testid="stNumberInputContainer"] input {
        font-size: 26px; font-weight: 800; color: #1D4ED8; letter-spacing: -0.01em;
        padding: 0; width: auto; flex: 0 0 auto; height: 36px;
        field-sizing: content; min-width: 1.2em; max-width: 4.5em;
    }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .asset-pct-marker)
        [data-testid="stNumberInputStepDown"],
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .asset-pct-marker)
        [data-testid="stNumberInputStepUp"] {
        border: 1px solid #E4EAF2 !important; border-radius: 8px !important;
        background: #FFFFFF !important; width: 28px; height: 28px;
    }
    .asset-pct-name { font-size: 14px; font-weight: 700; color: #0F172A; }
    .asset-pct-amt {
        font-size: 12px; color: #94A3B8; font-variant-numeric: tabular-nums;
        text-align: right; margin-top: -30px; margin-bottom: 8px;
    }
    .asset-pct-bar {
        height: 5px; background: #EFF3F9; border-radius: 3px; overflow: hidden;
        margin-top: 0; margin-bottom: 10px;
    }
    .section-cap { font-size: 13px; font-weight: 500; color: #64748B; margin-left: 10px; }

    /* 시연 옵션 — 목업처럼 연회색 배경 + 점선 테두리의 컴팩트한 박스 */
    [data-testid="stExpander"] { margin-top: 8px; }
    [data-testid="stExpander"] details {
        border: 1px dashed #CBD5E1; border-radius: 12px; background: #F8FAFC;
    }
    [data-testid="stExpander"] summary {
        color: #64748B; border-bottom: none !important;
    }
    [data-testid="stExpander"] summary:hover { color: #2563EB; }
    /* 펼친 내용: 라벨과 스테퍼를 한 줄로 나란히 (목업 배치) */
    [data-testid="stExpanderDetails"] [data-testid="stVerticalBlock"] {
        flex-direction: row; align-items: center; gap: 14px;
    }
    [data-testid="stExpanderDetails"] [data-testid="stElementContainer"] {
        width: auto !important;
    }
    [data-testid="stExpander"] [data-testid="stNumberInput"] { width: 150px; }

    /* 기본(primary) 버튼 — 목업처럼 크고 둥글게 */
    [data-testid="stBaseButton-primary"] {
        padding: 0.65rem 1.6rem; border-radius: 10px; font-weight: 700;
    }
    .asset-pct-bar div { height: 100%; background: #2563EB; border-radius: 3px; }

    .sum-box {
        display: flex; align-items: center; gap: 18px;
        border: 1px solid #BFDBFE; background: #F5F9FF; border-radius: 12px; padding: 14px 20px;
    }
    .sum-box.sum-box-warn { border-color: #FDE68A; background: #FFFBEB; }
    .sum-box .sum-icon {
        width: 34px; height: 34px; border-radius: 999px; display: flex; align-items: center;
        justify-content: center; font-size: 16px; font-weight: 800; color: #FFFFFF;
        background: #2563EB; flex-shrink: 0;
    }
    .sum-box-warn .sum-icon { background: #F59E0B; }
    .sum-box .sum-label-col { display: flex; flex-direction: column; gap: 2px; min-width: 150px; }
    .sum-box .sum-label { font-size: 12px; color: #64748B; }
    .sum-box .sum-num { font-size: 24px; font-weight: 800; color: #1D4ED8; font-variant-numeric: tabular-nums; }
    .sum-box-warn .sum-num { color: #B45309; }
    .sum-box .sum-total { font-size: 14px; font-weight: 700; color: #94A3B8; }
    .sum-box .sum-bar { height: 8px; background: rgba(148,163,184,0.18); border-radius: 4px; overflow: hidden; margin-bottom: 6px; }
    .sum-box .sum-bar div { height: 100%; background: #2563EB; border-radius: 4px; }
    .sum-box-warn .sum-bar div { background: #F59E0B; }
    .sum-box .sum-msg { font-size: 12px; font-weight: 700; color: #1D4ED8; }
    .sum-box-warn .sum-msg { color: #B45309; }

    .ips-grid {
        display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
        gap: 12px; margin-bottom: 12px;
    }
    .ips-card {
        border: 1px solid #E4EAF2; border-radius: 12px; background: #FFFFFF;
        padding: 12px 16px; display: flex; flex-direction: column; gap: 4px;
        min-width: 0;
    }
    .ips-card .ips-label {
        font-size: 11px; font-weight: 700; color: #94A3B8;
        letter-spacing: 0.06em; text-transform: uppercase;
    }
    .ips-card .ips-value {
        font-size: 14.5px; font-weight: 700; color: #0F172A;
        overflow-wrap: break-word; line-height: 1.45;
    }
    .ips-card.ips-warn { border-color: #FDE68A; background: #FFFBEB; }
    .ips-card.ips-warn .ips-value { color: #C2410C; }
    .ips-card.ips-wide { grid-column: 1 / -1; }
    .ips-card.ips-unique { border-color: #BFDBFE; background: #EFF6FF; }
    .ips-card.ips-unique .ips-label { color: #2563EB; }
    .ips-card.ips-unique .ips-value { color: #1D4ED8; }

    .pf-table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
    .pf-table th {
        background: #EFF6FF; color: #2563EB; font-size: 12.5px; font-weight: 700;
        padding: 10px 14px; text-align: left; white-space: nowrap;
        border: none;
    }
    .pf-table th:first-child { border-radius: 8px 0 0 8px; }
    .pf-table th:last-child { border-radius: 0 8px 8px 0; }
    .pf-table th.pf-num, .pf-table td.pf-num { text-align: right; font-variant-numeric: tabular-nums; }
    .pf-table td {
        padding: 11px 14px; font-size: 13.5px; color: #0F172A; font-weight: 600;
        border: none; border-bottom: 1px solid #EDF1F7;
    }
    .pf-table td.pf-weight { color: #2563EB; font-weight: 800; }

    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .section-card-marker) {
        background: #FFFFFF; border: 1px solid #E4EAF2; border-radius: 16px;
        box-shadow: 0 1px 2px rgba(15,23,42,0.04);
        padding: 22px 26px; margin-bottom: 18px;
    }

    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .pb-gate-marker) {
        border: 1.5px solid #2563EB; border-radius: 16px; padding: 0 24px 20px;
        background: #FFFFFF; box-shadow: 0 1px 2px rgba(37,99,235,0.08);
        margin-top: 8px; overflow: hidden;
    }
    /* 게이트 카드 헤더 밴드 — 좌우 패딩만 음수 마진으로 상쇄해 가장자리까지 채운다.
       카드에 overflow:hidden이 있어 모서리는 카드 radius로 잘린다. */
    .pb-gate-head {
        margin: 0 -24px 4px; padding: 14px 24px;
        background: #F0F5FF; border-bottom: 1px solid #DBE7FB;
        display: flex; align-items: center; gap: 12px;
    }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .pb-gate-marker)
        > div[data-testid="stElementContainer"]:first-child p {
        margin: 0;
    }
    .pb-gate-head .pb-gate-ico {
        width: 34px; height: 34px; border-radius: 10px; background: #2563EB;
        color: #FFFFFF; display: flex; align-items: center; justify-content: center;
        font-size: 16px; font-weight: 800; flex-shrink: 0;
    }
    .pb-gate-head .pb-gate-title {
        font-size: 15.5px; font-weight: 800; color: #2563EB; letter-spacing: -0.01em;
    }

    /* 파란 게이트 카드 안의 st.form 기본 테두리는 이중 테두리가 되므로 제거 */
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .pb-gate-marker)
        [data-testid="stForm"] {
        border: none; padding: 0;
    }

    @media (max-width: 900px) {
        .app-topbar { flex-wrap: wrap; row-gap: 8px; }
        .app-topbar .divider { display: none; }
        .report-header { padding: 18px 20px; gap: 16px; }
        .step-indicator { flex-basis: 100%; }
        .step-indicator .line { margin: 0 8px; }
    }

    /* 리포트 상단 히어로 카드 + KPI 스트립 */
    .report-hero {
        background: #FFFFFF; border: 1px solid #E4EAF2; border-radius: 16px;
        box-shadow: 0 1px 2px rgba(15,23,42,0.04);
        padding: 20px 24px; margin-bottom: 16px;
    }
    .report-hero h1 { font-size: 22px; font-weight: 800; color: #0F172A; margin: 0 0 4px; letter-spacing: -0.01em; }
    .report-hero p { color: #64748B; font-size: 13px; margin: 0 0 16px; }
    .kpi-strip { display: flex; flex-wrap: wrap; row-gap: 14px; border-top: 1px solid #EDF1F7; padding-top: 16px; }
    .kpi { flex: 1 1 150px; min-width: 150px; padding: 2px 18px; border-left: 1px solid #EDF1F7; }
    .kpi:first-child { border-left: none; padding-left: 0; }
    .kpi .kpi-label { font-size: 12px; color: #94A3B8; font-weight: 700; margin-bottom: 4px; }
    .kpi .kpi-value { font-size: 18px; font-weight: 800; color: #0F172A; letter-spacing: -0.01em; }
    .kpi .kpi-value.kpi-warn { color: #B45309; }
    .kpi .kpi-value.kpi-blue { color: #2563EB; }
    .kpi .kpi-value.kpi-gray { color: #64748B; }
    .kpi .kpi-sub { font-size: 11.5px; color: #94A3B8; margin-top: 3px; font-variant-numeric: tabular-nums; }

    /* 최신성 경고 — 노란 톤의 expander */
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .warn-exp-marker)
        [data-testid="stExpander"] details {
        border: 1px solid #FDE68A; background: #FFFBEB;
    }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .warn-exp-marker)
        [data-testid="stExpander"] summary {
        color: #B45309; font-weight: 700;
    }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .warn-exp-marker)
        [data-testid="stExpander"] summary:hover { color: #92400E; }
    /* 접힘/펼침을 알 수 있게 우측에 안내 텍스트 표시 */
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .warn-exp-marker)
        [data-testid="stExpander"] summary::after {
        content: "자세히 보기"; margin-left: auto; flex-shrink: 0;
        color: #B45309; font-size: 0.8rem; font-weight: 700; text-decoration: underline;
    }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .warn-exp-marker)
        [data-testid="stExpander"] details[open] summary::after {
        content: "접기";
    }

    .brand-header { padding: 0.4rem 0 1.2rem 0; margin-bottom: 0.4rem; }
    .brand-header .brand-row {
        display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.6rem;
    }
    .brand-header .wordmark {
        font-size: 1.6rem; font-weight: 800; color: #1B3B8F; letter-spacing: -0.01em;
    }
    .brand-header .wordmark .dot { color: #4D7FE0; }
    .brand-header .report-subtitle {
        font-size: 0.95rem; font-weight: 600; color: #444; margin-bottom: 0.3rem;
    }
    .brand-header p { color: #777; margin: 0; font-size: 0.85rem; }

    .section-title {
        border-left: 4px solid #1f6feb;
        padding-left: 0.6rem;
        margin: 0.2rem 0 0.8rem 0;
        font-size: 1.05rem;
        font-weight: 700;
        color: #1a1a1a;
    }

    .notice-box {
        border-left: 4px solid #c62828;
        background: #fdf3f3;
        border-radius: 6px;
        padding: 0.8rem 1rem;
        margin-bottom: 1rem;
        font-size: 0.9rem;
        color: #7a1f1f;
    }
    .notice-box strong { color: #c62828; }

    .status-tile {
        border-radius: 8px; padding: 0.7rem 1rem; text-align: left;
    }
    .status-tile .label { font-size: 0.85rem; color: #555; margin-bottom: 0.2rem; }
    .status-tile .value { font-size: 1.3rem; font-weight: 700; }
    .status-tile-blue { background: #e6f0ff; }
    .status-tile-blue .value { color: #0b4fbf; }
    .status-tile-gray { background: #eef0f3; }
    .status-tile-gray .value { color: #555; }
    .status-tile-yellow { background: #fff8e1; }
    .status-tile-yellow .value { color: #8a6d00; }

    .worst-tile {
        background: #fdecec; border: 1px solid #f3b8b8; border-radius: 10px;
        padding: 1rem 1.2rem; margin-bottom: 0.9rem;
    }
    .worst-tile .worst-label {
        display: inline-block; background: #c62828; color: white;
        border-radius: 999px; padding: 0.15rem 0.7rem; font-size: 0.78rem;
        font-weight: 700; margin-bottom: 0.5rem;
    }
    .worst-tile .worst-scenario { font-size: 1.15rem; font-weight: 700; color: #1a1a1a; }
    .worst-tile .worst-figures { font-size: 1rem; color: #333; margin-top: 0.3rem; }
    .worst-tile .worst-figures b { color: #c62828; }

    /* CVaR 기여도 — 목업풍 막대 */
    .dd-table td.dd-name { font-weight: 700; white-space: nowrap; }
    .dd-table td.dd-bar-cell { width: 40%; }
    .dd-bar { height: 12px; background: #EFF3F9; border-radius: 6px; overflow: hidden; }
    .dd-bar div { height: 100%; background: #2563EB; border-radius: 6px; }

    /* 스트레스 개별 시나리오 표 */
    .sc-table td.sc-name { font-weight: 700; white-space: nowrap; vertical-align: top; }
    .sc-table td.sc-desc { color: #334155; font-weight: 500; }
    .sc-table .sc-ref { font-size: 0.8rem; color: #94A3B8; margin-top: 4px; font-weight: 500; }
    .sc-table td.sc-loss { color: #DC2626; font-weight: 800; }

    /* 검증 체크리스트 아이콘 */
    .chk-ico {
        display: inline-flex; align-items: center; justify-content: center;
        width: 22px; height: 22px; border-radius: 999px;
        background: #EFF6FF; color: #2563EB; font-size: 13px; font-weight: 800;
    }
    .chk-ico.chk-ico-warn { background: #F59E0B; color: #FFFFFF; }
    .checks-table tr.check-row-warn td { background: #FFFBEB; }

    .checks-table { width: 100%; border-collapse: collapse; }
    .checks-table td {
        padding: 0.75rem 1rem; line-height: 1.6; font-size: 0.92rem;
        border-bottom: 1px solid #eee; vertical-align: middle;
    }
    .checks-table td:first-child { color: #222; }
    .checks-table td.check-col {
        width: 60px; text-align: center; font-size: 1.6rem; color: #222;
    }
    .checks-table th {
        text-align: left; padding: 0.75rem 1rem; font-size: 0.92rem;
        border-bottom: 2px solid #ddd; color: #555;
    }
    .checks-table th.check-col { text-align: center; }

    .basis-table { width: 100%; border-collapse: collapse; }
    .basis-table td {
        padding: 0.4rem 0.7rem; font-size: 0.82rem; color: #777;
        border-bottom: 1px solid #f0f0f0;
    }
    .basis-table td:first-child { color: #888; width: 30%; }

    [data-testid="stTableStyledTable"] th.col_heading {
        background: #eaf2ff; color: #0b4fbf; white-space: nowrap;
    }

    .warn-list {
        columns: 2; column-gap: 48px; margin: 4px 0 4px 0; padding-left: 1.2rem;
        color: #B45309; font-size: 0.9rem;
    }
    .warn-list li { margin-bottom: 6px; break-inside: avoid; }

    .warn-badge {
        display: inline-block; border: 1px solid #F59E0B; color: #B45309;
        background: #FFFBEB; border-radius: 999px; padding: 1px 9px;
        font-size: 0.72rem; font-weight: 700; margin-left: 8px;
        white-space: nowrap; vertical-align: 1px;
    }

    .empty-citation-box {
        border: 1px dashed #CBD5E1; border-radius: 12px; background: #FAFBFD;
        padding: 14px 18px; color: #94A3B8; font-size: 0.9rem; margin: 4px 0 10px;
    }

    /* AUDIT — 다크 네이비 푸터 카드 */
    .audit-box {
        background: #0F1B33; border-radius: 16px; padding: 20px 24px;
        color: #C7D2E5; font-size: 0.82rem; margin-top: 10px;
    }
    .audit-box .audit-head {
        display: flex; align-items: center; justify-content: space-between;
        flex-wrap: wrap; gap: 10px; margin-bottom: 12px;
    }
    .audit-box .audit-title {
        color: #7FA4E8; font-weight: 800; font-size: 0.85rem; letter-spacing: 0.04em;
    }
    .audit-box .audit-links { display: flex; gap: 10px; flex-wrap: wrap; }
    .audit-box a.audit-link {
        border: 1px solid #33436A; border-radius: 10px; padding: 6px 14px;
        color: #E2E8F0; text-decoration: none; font-weight: 700; font-size: 0.8rem;
    }
    .audit-box a.audit-link:hover { background: #1B2A4A; }
    .audit-box table { border-collapse: collapse; }
    .audit-box table td { padding: 3px 0; vertical-align: top; }
    .audit-box table td:first-child {
        color: #8DA2C0; width: 150px; padding-right: 14px; white-space: nowrap;
    }
    .audit-box .mono2 {
        font-family: "SFMono-Regular", Consolas, monospace;
        color: #E2E8F0; font-size: 0.8rem; line-height: 1.6; word-break: break-all;
    }
    .audit-box .audit-divider { border-top: 1px solid #22314F; margin: 14px 0; }
    .audit-box .audit-disclaimer { color: #8DA2C0; line-height: 1.6; }

    .footer-box {
        background: #f6f7f9; border-radius: 10px; padding: 1rem 1.2rem;
        font-size: 0.82rem; color: #555;
    }
    .footer-box .mono {
        font-family: "SFMono-Regular", Consolas, monospace;
        color: #333; font-size: 0.8rem; line-height: 1.6;
        word-break: break-all;
    }
    .footer-box .basis-table td:first-child { width: 22%; }

    .citation-table { width: 100%; table-layout: fixed; border-collapse: collapse; }
    .citation-table th, .citation-table td {
        padding: 0.6rem 0.8rem; font-size: 0.88rem; line-height: 1.6;
        border-bottom: 1px solid #eee; text-align: left; vertical-align: top;
        overflow-wrap: anywhere;
    }
    .citation-table td:nth-child(2) { word-break: break-all; }
    .citation-table a.doc-link {
        color: #1f6feb; text-decoration: none;
        display: inline-flex; align-items: center; gap: 0.3rem;
    }
    .citation-table a.doc-link:hover { text-decoration: underline; }
    .citation-table .doc-link-icon { flex-shrink: 0; }
    .citation-table th {
        background: #eaf2ff; color: #0b4fbf; border-bottom: 2px solid #ddd;
    }
    .citation-table col.col-quote { width: 55%; }
    .citation-table col.col-source { width: 28%; }
    .citation-table col.col-date { width: 17%; }

    @media print {
        /* PDF 추출 시에는 화면용 연파랑 배경을 흰색으로 되돌린다 */
        body, .stApp, [data-testid="stAppViewContainer"],
        [data-testid="stHeader"], [data-testid="stMain"] {
            background: #FFFFFF !important;
        }
        /* 최신성 경고 상세는 인쇄 시 자동으로 펼쳐 보여준다 */
        div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .warn-exp-marker)
            [data-testid="stExpander"] details:not([open])::details-content {
            content-visibility: visible !important; display: block !important;
        }
        div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .warn-exp-marker)
            [data-testid="stExpander"] details:not([open]) > *:not(summary) {
            display: block !important;
        }
        /* 인쇄물에는 접기/펼치기 안내 텍스트를 숨긴다 */
        div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .warn-exp-marker)
            [data-testid="stExpander"] summary::after {
            content: "" !important;
        }
        /* 근본 원인: Streamlit은 세로 블록을 display:flex(column)로 그리는데,
           Chrome 인쇄 엔진은 flex 컨테이너 내부의 페이지 분할을 제대로 못 해
           자식 요소가 다음 페이지 요소와 겹쳐 그려진다. 인쇄 시에는 일반
           block 레이아웃으로 되돌려야 break-inside 규칙이 정상 동작한다.
           단, 모든 stVerticalBlock에 걸면 st.metric 등 내부 컴포넌트가 쓰는
           flex 간격(라벨-값 수직 배치 등)까지 틀어지므로, 우리가 만든
           섹션 컨테이너(.section-title을 가진 블록)에만 한정한다. */
        div[data-testid="stVerticalBlock"]:has(.section-title) {
            display: block;
        }
        /* display:block으로 바뀌면 원래 flex의 gap:15px가 무효화되어 요소들이
           다 붙어버린다 — 직계 자식에 margin-bottom으로 같은 간격을 되살린다. */
        div[data-testid="stVerticalBlock"]:has(.section-title) > * {
            margin-bottom: 15px;
        }
        div[data-testid="stVerticalBlock"]:has(.section-title) > *:last-child {
            margin-bottom: 0;
        }
        /* 좌우 배치(st.columns)는 block으로 바꾸면 세로로 쌓여 레이아웃이
           바뀌므로, flex는 유지하되 행 전체가 페이지 중간에서 안 쪼개지게만
           한다 — 두 타일/지표 정도의 짧은 콘텐츠라 항상 만족 가능하다. */
        div[data-testid="stHorizontalBlock"] {
            break-inside: avoid;
            page-break-inside: avoid;
        }
        /* 짧은 요약 섹션은 통째로 유지한다. 단, checks-table/citation-table처럼
           한 페이지보다 길어질 수 있는 표를 포함한 섹션에 이 규칙을 걸면
           불가능한 제약이 되어 인쇄 엔진이 다음 요소(footer-box 등)를
           표 끝부분과 겹쳐 그리는 버그가 생긴다 — 그런 섹션은 제외한다. */
        div[data-testid="stVerticalBlock"]:has(.section-title):not(:has(.checks-table)):not(:has(.citation-table)) {
            break-inside: avoid;
            page-break-inside: avoid;
        }
        .footer-box, .audit-box, .checks-table tr, .basis-table tr, .citation-table tr {
            break-inside: avoid;
            page-break-inside: avoid;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def section_title(text: str) -> None:
    st.markdown(f'<div class="section-title">{text}</div>', unsafe_allow_html=True)


SCENARIO_LABELS = {
    "A_high_rate": "고금리 충격",
    "B_strong_usd": "강달러 충격",
    "C_covid": "코로나 충격",
}

ASSET_LABELS = dict(ASSET_DEFINITIONS)

LINK_ICON_SVG = (
    '<svg class="doc-link-icon" viewBox="0 0 16 16" width="12" height="12" '
    'fill="none" stroke="currentColor" stroke-width="1.6">'
    '<path d="M6.5 9.5 14 2M9 2h5v5M13 9v4a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h4"/>'
    "</svg>"
)


def scenario_label(code: str | None) -> str:
    if not code:
        return "-"
    return SCENARIO_LABELS.get(code, code)


def format_krw(val, suffix: str = "원") -> str:
    if val is None:
        return "-"
    return f"{val:,.0f}{suffix}"


def format_pct(val) -> str:
    if val is None:
        return "-"
    return f"{val:.1%}"


def format_range(low, high, point=None) -> str:
    """신뢰구간이 있으면 범위로, 없으면(구엔진 등) 점추정치로 폴백한다.

    위조정밀도 방지를 위해 "약 X원"이 아니라 구간으로 보여주는 게
    목적이라, low/high가 있으면 point는 무시한다.
    """
    if low is not None and high is not None:
        return f"{format_krw(low)} ~ {format_krw(high)}"
    return format_krw(point)


def format_pct_range(low, high, point=None) -> str:
    """format_range와 동일한 규칙을 비율(%)에 적용한다."""
    if low is not None and high is not None:
        return f"{format_pct(low)} ~ {format_pct(high)}"
    return format_pct(point)


DEFAULT_PERCENTAGES = {
    item["asset_class"]: item["weight"] * 100 for item in DUMMY_PORTFOLIO
}

report = st.session_state.get("report")

if not report:
    st.markdown(
        f"""
        <div class="app-topbar">
        <img src="data:image/png;base64,{LOGO_B64}" alt="Symphony">
        <div class="divider"></div>
        <div class="title">재현가능·설명가능 리스크 리포트</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _current_step = 3 if st.session_state.get("pending_state") else 1
    _steps = ["상담 입력", "IPS 추출", "포트폴리오 비중", "PB 승인"]
    _step_html = "".join(
        f'<div class="step"><div class="num {"num-done" if n <= _current_step else "num-pending"}">{n}</div>'
        f'<div class="label">{html.escape(label)}</div></div>'
        + ('<div class="line"></div>' if n < len(_steps) else "")
        for n, label in enumerate(_steps, start=1)
    )
    st.markdown(
        f"""
        <div class="report-header">
        <div class="titles">
        <h1>고객 상담 및 포트폴리오 입력</h1>
        <p>상담 내역에서 IPS를 추출하고 PB 승인 후에만 계산을 진행합니다.</p>
        </div>
        <div class="step-indicator">{_step_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container():
        st.markdown('<span class="section-card-marker"></span>', unsafe_allow_html=True)
        section_title("1. 고객 상담")
        raw_input = st.text_area(
            "상담 내용",
            value=SAMPLE_RAW_INPUT,
            height=150,
        )
        fixed_cols = st.columns(5)
        for _col, _label, _value in zip(
            fixed_cols,
            ["Age", "Job", "Asset (억 원)", "Risk", "Goal"],
            [FIXED_AGE, FIXED_JOB, f"{FIXED_ASSET_EOK:g}", FIXED_RISK, FIXED_GOAL],
            strict=True,
        ):
            _col.markdown(
                f'<div class="fixed-field-card">'
                f'<div class="fixed-label">{html.escape(_label)}</div>'
                f'<div class="fixed-value">{html.escape(str(_value))}</div>'
                "</div>",
                unsafe_allow_html=True,
            )

    with st.container():
        st.markdown('<span class="section-card-marker"></span>', unsafe_allow_html=True)
        st.markdown(
            '<div class="section-title">2. 포트폴리오 비중 '
            '<span class="section-cap">6개 자산군 비중을 입력해 주세요. (합계 100% 기준)</span></div>',
            unsafe_allow_html=True,
        )
        percentages: dict[str, float] = {}
        cols = st.columns(3)
        for idx, (asset_class, name) in enumerate(ASSET_DEFINITIONS):
            col = cols[idx % 3]
            with col.container(border=False):
                st.markdown('<span class="asset-pct-marker"></span>', unsafe_allow_html=True)
                st.markdown(
                    f'<span class="asset-pct-name">{html.escape(name)}</span>',
                    unsafe_allow_html=True,
                )
                percentages[asset_class] = st.number_input(
                    f"{name} (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(DEFAULT_PERCENTAGES[asset_class]),
                    step=1.0,
                    label_visibility="collapsed",
                )
                _amt = percentages[asset_class] / 100.0 * TOTAL_ASSET_KRW
                st.markdown(
                    f'<div class="asset-pct-amt">= {_amt:,.0f}원</div>'
                    f'<div class="asset-pct-bar"><div style="width:{min(100.0, percentages[asset_class]):.2f}%;"></div></div>',
                    unsafe_allow_html=True,
                )
        total_pct = sum(percentages.values())
        _ok = abs(total_pct - 100.0) < 1e-6
        if _ok:
            _sum_msg = "합계 100% 충족 — IPS 추출 가능"
        elif total_pct > 100:
            _sum_msg = f"합계 100% 초과 — {total_pct - 100:g}%p 초과"
        else:
            _sum_msg = f"합계 100% 미만 — {100 - total_pct:g}%p 부족"
        st.markdown(
            f'<div class="sum-box{"" if _ok else " sum-box-warn"}">'
            f'<div class="sum-icon">{"✓" if _ok else "!"}</div>'
            '<div class="sum-label-col">'
            '<div class="sum-label">현재 합계</div>'
            f'<div><span class="sum-num">{total_pct:g}%</span><span class="sum-total"> / 100%</span></div>'
            "</div>"
            '<div style="flex:1;">'
            f'<div class="sum-bar"><div style="width:{min(100.0, total_pct):.2f}%;"></div></div>'
            f'<div class="sum-msg">{html.escape(_sum_msg)}</div>'
            "</div></div>",
            unsafe_allow_html=True,
        )

        with st.expander("시연 옵션"):
            st.caption("judge 강제 실패 횟수")
            force_judge_fail = st.number_input(
                "judge 강제 실패 횟수",
                min_value=0, max_value=5, value=0,
                label_visibility="collapsed",
            )

        prepare_clicked = st.button("IPS 추출", type="primary")

    if prepare_clicked:
        try:
            portfolio = portfolio_from_percentages(percentages)
            graph = build_graph()
            invocation = prepare_trace_invocation(
                {"configurable": {"thread_id": str(uuid.uuid4())}},
                phase="input",
            )
            payload = {
                "raw_input": raw_input,
                "portfolio": portfolio,
                "demo_options": {"force_judge_fail": int(force_judge_fail)},
                "run_config": {"observability": invocation.observability},
            }
            if invocation.trace_id:
                payload["trace_id"] = invocation.trace_id
            with st.spinner("상담 내역 분석 및 IPS 항목 추출 중…"):
                with tracing_scope(invocation):
                    for _ in graph.stream(
                        payload,
                        invocation.config,
                        stream_mode="updates",
                    ):
                        pass
            config = invocation.config
            snapshot = graph.get_state(config)
            if not (snapshot.next and "approval_gate" in snapshot.next):
                raise RuntimeError("그래프가 PB 승인 게이트에서 정지하지 않았습니다.")
            st.session_state["pending_graph"] = graph
            st.session_state["pending_config"] = config
            st.session_state["pending_state"] = dict(snapshot.values)
            st.rerun()
        except Exception as exc:
            st.error(f"IPS 추출 또는 입력 검증에 실패했습니다: {exc}")

    pending = st.session_state.get("pending_state")
    if pending:
        with st.container():
            st.markdown('<span class="section-card-marker"></span>', unsafe_allow_html=True)
            section_title("3. IPS 및 PB 승인")
            _ips_items = list((pending.get("ips") or {}).items())
            _small = [(k, v) for k, v in _ips_items if k not in ("Goal", "Unique")]
            _wide = [(k, v) for k, v in _ips_items if k in ("Goal", "Unique")]
            _ips_cards = []
            for key, value in _small + _wide:
                _cls = "ips-card"
                if key in ("Goal", "Unique"):
                    _cls += " ips-wide"
                if key == "Unique":
                    _cls += " ips-unique"
                elif str(value).strip() == "확인 필요":
                    _cls += " ips-warn"
                _ips_cards.append(
                    f'<div class="{_cls}">'
                    f'<div class="ips-label">{html.escape(str(key))}</div>'
                    f'<div class="ips-value">{html.escape(str(value))}</div>'
                    "</div>"
                )
            st.markdown(
                f'<div class="ips-grid">{"".join(_ips_cards)}</div>',
                unsafe_allow_html=True,
            )
            portfolio_rows = [
                {
                    "자산군": item["name"],
                    "금액": format_krw(item["value_krw"]),
                    "비중": format_pct(item["weight"]),
                }
                for item in (pending.get("portfolio") or [])
                if isinstance(item, dict)
            ]
            _pf_body = "".join(
                "<tr>"
                f'<td>{html.escape(str(row["자산군"]))}</td>'
                f'<td class="pf-num">{html.escape(str(row["금액"]))}</td>'
                f'<td class="pf-num pf-weight">{html.escape(str(row["비중"]))}</td>'
                "</tr>"
                for row in portfolio_rows
            )
            st.markdown(
                '<table class="pf-table">'
                '<thead><tr><th>자산군</th><th class="pf-num">금액</th>'
                '<th class="pf-num">비중</th></tr></thead>'
                f"<tbody>{_pf_body}</tbody></table>",
                unsafe_allow_html=True,
            )

            conflicts = pending.get("conflicts") or []
            blocking_conflicts = [
                conflict for conflict in conflicts if conflict["severity"] == "block"
            ]
            review_conflicts = [
                conflict for conflict in conflicts if conflict["severity"] == "review"
            ]
            if conflicts:
                if blocking_conflicts:
                    st.error("예외 승인할 수 없는 IPS 충돌이 있어 입력 보완이 필요합니다.")
                else:
                    st.warning("PB의 구체적 사유가 있는 예외 승인 후 리스크 계산만 진행할 수 있습니다.")
                st.dataframe(conflicts, use_container_width=True, hide_index=True)
            approve_clicked = False
            if not blocking_conflicts:
                with st.container():
                    st.markdown(
                        '<span class="pb-gate-marker"></span>'
                        '<div class="pb-gate-head">'
                        '<div class="pb-gate-ico">✓</div>'
                        '<div class="pb-gate-title">PB 승인</div>'
                        "</div>",
                        unsafe_allow_html=True,
                    )
                    with st.form("pb_approval"):
                        ips = pending.get("ips") or {}
                        unique_text = st.text_input(
                            "Unique 수정",
                            value=ips.get("Unique", ""),
                        )
                        _c_name, _c_id = st.columns(2)
                        with _c_name:
                            approver_name = st.text_input("PB 이름", placeholder="PB 이름 입력")
                        with _c_id:
                            approver_employee_id = st.text_input(
                                "PB 사번",
                                placeholder="6자리 사번 입력",
                                max_chars=6,
                            )
                        note = st.text_area("승인 의견", placeholder="검토 의견을 입력하세요.")
                        exception_reason = ""
                        if review_conflicts:
                            exception_reason = st.text_area(
                                "예외 승인 사유 (필수, 10자 이상)",
                                placeholder="충돌을 인지하고도 리스크 계산이 필요한 이유와 보완 조치를 기록하세요.",
                            )
                        approve_clicked = st.form_submit_button("승인 및 리스크 분석 실행", type="primary")

            if approve_clicked:
                approver_error = validate_pb_approver(
                    approver_name,
                    approver_employee_id,
                )
                if approver_error:
                    st.error(approver_error)
                elif review_conflicts and len(exception_reason.strip()) < 10:
                    st.error("예외 승인 사유를 10자 이상 입력해야 합니다.")
                else:
                    try:
                        graph = st.session_state["pending_graph"]
                        config = st.session_state["pending_config"]
                        resume_invocation = prepare_trace_invocation(
                            config,
                            phase="analysis",
                            trace_id=pending.get("trace_id"),
                        )
                        resume_run_config = dict(pending.get("run_config") or {})
                        resume_run_config["observability"] = merge_observability(
                            resume_run_config.get("observability"),
                            resume_invocation.observability,
                        )
                        checkpoint_config = {
                            "configurable": resume_invocation.config["configurable"],
                        }
                        reviewed_ips = IPSProfile.model_validate(
                            {**ips, "Unique": unique_text}
                        ).model_dump()
                        graph.update_state(
                            checkpoint_config,
                            {
                                "run_config": resume_run_config,
                                "ips": reviewed_ips,
                                "approval": {
                                    "status": "reviewed",
                                    "decision": (
                                        "exception_approved"
                                        if review_conflicts
                                        else "approved"
                                    ),
                                    "approver": approver_label(
                                        approver_name,
                                        approver_employee_id,
                                    ),
                                    "note": note.strip(),
                                    "exception_reason": exception_reason.strip(),
                                },
                            },
                        )
                        with st.spinner("포트폴리오 리스크 연산 및 리포트 생성 중…"):
                            with tracing_scope(resume_invocation):
                                for _ in graph.stream(
                                    None,
                                    resume_invocation.config,
                                    stream_mode="updates",
                                ):
                                    pass
                        st.session_state["report"] = graph.get_state(
                            resume_invocation.config
                        ).values.get("report")
                        for key in ("pending_graph", "pending_config", "pending_state"):
                            st.session_state.pop(key, None)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"PB 승인 및 리스크 분석 중 오류가 발생했습니다: {exc}")

report = st.session_state.get("report")

if not report:
    st.stop()
else:
    total_value = report["summary"]["portfolio"]["total_value_krw"]
    st.markdown(
        f"""
        <div class="app-topbar">
        <img src="data:image/png;base64,{LOGO_B64}" alt="Symphony">
        <div class="divider"></div>
        <div class="title">재현가능·설명가능 리스크 리포트</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    risk = report.get("summary", {}).get("risk", {})
    warnings = report.get("warnings") or []
    _governance = report["governance"]
    _judge_passed = _governance["judge_passed"]
    if _judge_passed and warnings:
        _judge_value, _judge_tone = "조건부 통과 (수동검토 필요)", "kpi-warn"
    elif _judge_passed:
        _judge_value, _judge_tone = "통과", "kpi-blue"
    else:
        _judge_value, _judge_tone = "검토 필요", "kpi-gray"

    def _kpi(label: str, value: str, sub: str = "", tone: str = "") -> str:
        _sub = f'<div class="kpi-sub">{sub}</div>' if sub else ""
        return (
            f'<div class="kpi"><div class="kpi-label">{label}</div>'
            f'<div class="kpi-value {tone}">{value}</div>{_sub}</div>'
        )

    _kpis = (
        _kpi(
            "VaR (1일)",
            format_pct_range(risk.get("var_1d_pct_low"), risk.get("var_1d_pct_high"), risk.get("var_1d_pct")),
            format_range(risk.get("var_1d_krw_low"), risk.get("var_1d_krw_high"), risk.get("var_1d_krw")),
        )
        + _kpi(
            "VaR (10일)",
            format_pct_range(risk.get("var_10d_pct_low"), risk.get("var_10d_pct_high"), risk.get("var_10d_pct")),
            format_range(risk.get("var_10d_krw_low"), risk.get("var_10d_krw_high"), risk.get("var_10d_krw")),
        )
        + _kpi(
            "CVaR (1일)",
            format_pct_range(risk.get("cvar_1d_pct_low"), risk.get("cvar_1d_pct_high"), risk.get("cvar_1d_pct")),
            format_range(risk.get("cvar_1d_krw_low"), risk.get("cvar_1d_krw_high"), risk.get("cvar_1d_krw")),
        )
        + _kpi(
            "CVaR (10일)",
            format_pct_range(risk.get("cvar_10d_pct_low"), risk.get("cvar_10d_pct_high"), risk.get("cvar_10d_pct")),
            format_range(risk.get("cvar_10d_krw_low"), risk.get("cvar_10d_krw_high"), risk.get("cvar_10d_krw")),
        )
        + _kpi("리포트 신뢰성 검증", _judge_value, tone=_judge_tone)
        + _kpi("기준일", str(report.get("as_of_date") or "-"), tone="kpi-blue")
    )
    st.markdown(
        f"""
        <div class="report-hero">
        <h1>{report["title"]}</h1>
        <p>기준일 {report["as_of_date"] or "-"} · 포트폴리오 총액 {format_krw(total_value)}</p>
        <div class="kpi-strip">{_kpis}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if warnings:
        # judge가 여러 건을 한 문자열로 합쳐 주는 경우가 있어 "#n" 경계로 나눠 센다.
        _warn_items: list[str] = []
        for w in warnings:
            _warn_items.extend(
                s.strip() for s in re.split(r",\s(?=#)", str(w)) if s.strip()
            )

        # "#N"은 judge의 검증 통과 인용 인덱스(1-base) — 같은 기준으로 재구성해
        # 사용자에게는 인덱스 대신 문서명을 보여준다.
        def _judge_verified(citation) -> bool:
            return (
                isinstance(citation, dict)
                and citation.get("verified") is True
                and str(citation.get("quote") or "").strip() != ""
                and str(citation.get("source") or "").strip() != ""
                and str(citation.get("chunk_id") or "").strip() != ""
            )

        _verified_sources = [
            str(c.get("source")).replace("\\", "/").rsplit("/", 1)[-1]
            for c in (report.get("citations") or [])
            if _judge_verified(c)
        ]

        def _warn_display(item: str) -> str:
            match = re.match(r"#(\d+)\s+(?:house_view\s+)?(.*)$", item)
            if not match:
                return item
            idx = int(match.group(1))
            if 1 <= idx <= len(_verified_sources):
                return f"{_verified_sources[idx - 1]} — {match.group(2)}"
            return item

        _warn_items = [_warn_display(i) for i in _warn_items]
        with st.container():
            st.markdown('<span class="warn-exp-marker"></span>', unsafe_allow_html=True)
            with st.expander(
                f"최신성 경고 {len(_warn_items)}건(수동검토 필요)",
                icon=":material/warning:",
            ):
                st.markdown(
                    '<ul class="warn-list">'
                    + "".join(f"<li>{html.escape(i)}</li>" for i in _warn_items)
                    + "</ul>",
                    unsafe_allow_html=True,
                )
    grouped_citations = group_verified_citations(report.get("citations") or [])

    def _render_citation_section(section: dict, *, heading_override: str | None = None) -> None:
        category = section["category"]
        section_citations = grouped_citations[category]
        if heading_override is not None:
            st.markdown(
                f'<div style="font-size:1.05rem;font-weight:700;color:#1a1a1a;'
                f'margin:0.6rem 0 0.5rem 0;">{html.escape(heading_override)}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="font-size:1.05rem;font-weight:700;color:#1a1a1a;'
                f'margin:0.6rem 0 0.2rem 0;">{html.escape(section["title"])}</div>',
                unsafe_allow_html=True,
            )
        if section.get("description"):
            st.caption(section["description"])
        if not section_citations:
            st.markdown(
                '<div class="empty-citation-box">'
                "현재 포트폴리오 조건에 해당하는 인용 정보가 없습니다.</div>",
                unsafe_allow_html=True,
            )
            return
        rows = citation_table_rows(section_citations)

        def _is_stale(citation: dict) -> bool:
            """judge의 house_view 최신성 기준(6개월 초과)을 표시용으로만 재현한다."""
            if category != "house_view":
                return False
            extra = citation.get("extra")
            extra = extra if isinstance(extra, dict) else {}
            try:
                published = date.fromisoformat(str(extra.get("published_at")))
                reference = date.fromisoformat(str(report.get("as_of_date")))
            except (TypeError, ValueError):
                return False
            months = (reference.year - published.year) * 12 + reference.month - published.month
            return months > 6

        stale_flags = [
            _is_stale(citation)
            for citation in section_citations
            if isinstance(citation, dict)
        ]

        def _source_cell(source: str) -> str:
            escaped = html.escape(source)
            url = document_url(source)
            if not url:
                return escaped
            return (
                f'<a class="doc-link" href="{html.escape(url)}" '
                f'target="_blank" rel="noopener">{LINK_ICON_SVG}'
                f"{escaped}</a>"
            )

        body = "".join(
            "<tr>"
            f"<td>{html.escape(str(row['근거문장']))}</td>"
            f"<td>{_source_cell(str(row['출처']))}</td>"
            f"<td>{html.escape(str(row['발행기준일']))}"
            + ('<span class="warn-badge">경고</span>' if stale else "")
            + "</td></tr>"
            for row, stale in zip(rows, stale_flags, strict=True)
        )
        st.markdown(
            '<table class="citation-table">'
            '<colgroup>'
            '<col class="col-quote">'
            '<col class="col-source"><col class="col-date">'
            "</colgroup>"
            "<thead><tr><th>인용 문장</th>"
            "<th>출처</th><th>발행일</th></tr></thead>"
            f"<tbody>{body}</tbody></table>",
            unsafe_allow_html=True,
        )

    with st.container():
        st.markdown('<span class="section-card-marker"></span>', unsafe_allow_html=True)
        ci_level = risk.get("ci_level")
        section_title("최대 손실 위험 지표 (VaR / CVaR, 신뢰수준 99%)")
        if ci_level is not None:
            st.caption(f"오차 범위 {ci_level:.0%} 신뢰구간 기준")
        _var_rows = "".join(
            "<tr>"
            f"<td>{period}</td>"
            f'<td class="pf-num">{format_range(risk.get(f"var_{p}_krw_low"), risk.get(f"var_{p}_krw_high"), risk.get(f"var_{p}_krw"))}</td>'
            f'<td class="pf-num pf-weight">{format_pct_range(risk.get(f"var_{p}_pct_low"), risk.get(f"var_{p}_pct_high"), risk.get(f"var_{p}_pct"))}</td>'
            f'<td class="pf-num">{format_range(risk.get(f"cvar_{p}_krw_low"), risk.get(f"cvar_{p}_krw_high"), risk.get(f"cvar_{p}_krw"))}</td>'
            f'<td class="pf-num pf-weight">{format_pct_range(risk.get(f"cvar_{p}_pct_low"), risk.get(f"cvar_{p}_pct_high"), risk.get(f"cvar_{p}_pct"))}</td>'
            "</tr>"
            for period, p in (("1일", "1d"), ("10일", "10d"))
        )
        st.markdown(
            '<table class="pf-table">'
            "<thead><tr><th>기간</th>"
            '<th class="pf-num">VaR (금액)</th><th class="pf-num">VaR (수익률)</th>'
            '<th class="pf-num">CVaR (금액)</th><th class="pf-num">CVaR (수익률)</th>'
            "</tr></thead>"
            f"<tbody>{_var_rows}</tbody></table>",
            unsafe_allow_html=True,
        )

        data_period = risk.get("data_period") or {}
        methodology_ref = risk.get("methodology_ref")
        n_obs = data_period.get("n_observations")
        n_obs_text = f" ({n_obs}거래일)" if n_obs is not None else ""
        period_text = (
            f"{data_period.get('start')} ~ {data_period.get('end')}{n_obs_text}"
            if data_period.get("start") and data_period.get("end")
            else "정보 없음"
        )
        methodology_text = f"{methodology_ref}.pdf" if methodology_ref else "정보 없음"
        fx_rate_asof = risk.get("fx_rate_asof")
        fx_rate_text = f"{fx_rate_asof:,.2f}원" if fx_rate_asof is not None else "정보 없음"
        _basis_table_html = (
            '<div style="font-size:0.78rem;font-weight:700;color:#999;'
            'margin:0.6rem 0 0.2rem 0;">산출 근거</div>'
            '<table class="basis-table">'
            f'<tr><td>관측 데이터 기간</td><td>{html.escape(period_text)}</td></tr>'
            f'<tr><td>적용 환율</td><td>{html.escape(fx_rate_text)}</td></tr>'
            f'<tr><td>방법론</td><td>{html.escape(methodology_text)}</td></tr>'
            "</table>"
        )
        st.markdown(_basis_table_html, unsafe_allow_html=True)
        _methodology_section = next(
            s for s in RAG_EVIDENCE_SECTIONS if s["category"] == "methodology"
        )
        _render_citation_section(_methodology_section, heading_override="정량 계산 방법론")

    drilldown = risk.get("drilldown") or []
    if drilldown:
        with st.container():
            st.markdown('<span class="section-card-marker"></span>', unsafe_allow_html=True)
            section_title("CVaR 자산군별 기여도")
            st.caption(
                "최악 1% 구간에서 각 자산군이 CVaR에 기여한 정도  \n"
                "\\+ 손실 위험 증가 / − 손실 위험 완화"
            )
            _max_contrib = max(
                (abs(row["contribution_pct"] or 0) for row in drilldown), default=0
            )
            _dd_rows = "".join(
                "<tr>"
                f'<td class="dd-name">{html.escape(ASSET_LABELS.get(row["asset_class"], row["asset_class"]))}</td>'
                '<td class="dd-bar-cell"><div class="dd-bar">'
                f'<div style="width:{(abs(row["contribution_pct"] or 0) / _max_contrib * 100) if _max_contrib else 0:.1f}%;"></div>'
                "</div></td>"
                f'<td class="pf-num">{format_krw(row["contribution_krw"])}</td>'
                f'<td class="pf-num pf-weight">{format_pct(row["contribution_pct"])}</td>'
                "</tr>"
                for row in drilldown
            )
            st.markdown(
                '<table class="pf-table dd-table">'
                "<thead><tr><th>자산군</th><th></th>"
                '<th class="pf-num">기여 금액</th><th class="pf-num">기여 비중</th></tr></thead>'
                f"<tbody>{_dd_rows}</tbody></table>",
                unsafe_allow_html=True,
            )

    with st.container():
        st.markdown('<span class="section-card-marker"></span>', unsafe_allow_html=True)
        section_title("스트레스 테스트")
        scenario_count = risk.get("stress_scenario_count", 0)
        worst_scenario_code = risk.get("stress_scenario")
        worst_loss = format_range(
            risk.get("stress_loss_krw_low"),
            risk.get("stress_loss_krw_high"),
            risk.get("stress_loss_krw"),
        )
        worst_loss_pct = format_pct_range(
            risk.get("stress_loss_pct_low"),
            risk.get("stress_loss_pct_high"),
            risk.get("stress_loss_pct"),
        )
        st.markdown(
            f"""
            <div class="worst-tile">
            <span class="worst-label">최악 시나리오 (전체 {scenario_count}건 중)</span>
            <div class="worst-scenario">{scenario_label(worst_scenario_code)}</div>
            <div class="worst-figures">손실액 <b>{worst_loss}</b> · 손실률 <b>{worst_loss_pct}</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        scenarios = risk.get("stress_scenarios") or []
        if scenarios:
            st.caption(f"개별 시나리오 비교 (전체 {scenario_count}건)")
            _sc_rows = "".join(
                "<tr>"
                f'<td class="sc-name">{html.escape(str(scenario_label(sc["scenario"])))}</td>'
                '<td class="sc-desc">'
                f'{html.escape(str(sc["description"] or ""))}'
                f'<div class="sc-ref">{html.escape(str(sc["reference"] or ""))}</div>'
                "</td>"
                f'<td class="pf-num">{format_range(sc["loss_krw_low"], sc["loss_krw_high"], sc["loss_krw"])}</td>'
                f'<td class="pf-num sc-loss">{format_pct_range(sc["loss_pct_low"], sc["loss_pct_high"], sc["loss_pct"])}</td>'
                "</tr>"
                for sc in scenarios
            )
            st.markdown(
                '<table class="pf-table sc-table">'
                "<thead><tr><th>시나리오</th><th>설명 · 근거</th>"
                '<th class="pf-num">손실액(범위)</th><th class="pf-num">손실률(범위)</th>'
                "</tr></thead>"
                f"<tbody>{_sc_rows}</tbody></table>",
                unsafe_allow_html=True,
            )

    with st.container():
        st.markdown('<span class="section-card-marker"></span>', unsafe_allow_html=True)
        section_title("분석 근거 및 원문 출처")
        evidence = report.get("evidence", {})
        e1, e2 = st.columns(2)
        e1.metric("유효한 검증 근거", f"{evidence.get('verified_citation_count', 0)}건")
        e2.metric("전체 참조 자료", f"{evidence.get('citation_count', 0)}건")

        for section in RAG_EVIDENCE_SECTIONS:
            if section["category"] == "methodology":
                continue
            _render_citation_section(section)

    with st.container():
        st.markdown('<span class="section-card-marker"></span>', unsafe_allow_html=True)
        section_title("리포트 신뢰성 검증")
        st.caption(
            "Judge LLM 및 LangSmith를 기반으로 리스크 연산 결과를 검증합니다. "
            "리스크 분석 및 검증 결과는 모두 기록됩니다."
        )
        governance = report.get("governance", {})
        judge = report.get("judge", {})
        judge_passed = governance.get("judge_passed")
        gate_on = governance.get("strict_citation_gate")

        def status_tile(label: str, value: str, tone: str) -> str:
            return (
                f'<div class="status-tile status-tile-{tone}">'
                f'<div class="label">{label}</div><div class="value">{value}</div></div>'
            )

        if judge_passed and warnings:
            # 상단 "확인 필요" 경고와 같은 사실을 반대로 말하지 않도록,
            # 통과했지만 수동검토가 필요한 상태는 별도 톤으로 구분한다.
            judge_label, judge_tone = "조건부 통과 (수동검토 필요)", "yellow"
        elif judge_passed:
            judge_label, judge_tone = "통과", "blue"
        else:
            judge_label, judge_tone = "검토 필요", "gray"

        t1, t2 = st.columns(2)
        t1.markdown(
            status_tile("리포트 신뢰성 검증", judge_label, judge_tone),
            unsafe_allow_html=True,
        )
        t2.markdown(
            status_tile("검증 강도", "엄격" if gate_on else "표준", "blue" if gate_on else "gray"),
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)
        checks = judge.get("checks") or []
        if checks:
            rows = "".join(
                f'<tr{"" if c.get("passed") else " class=\"check-row-warn\""}>'
                f'<td>{html.escape(str(c.get("detail") or ""))}</td>'
                f'<td class="check-col">'
                f'<span class="chk-ico{"" if c.get("passed") else " chk-ico-warn"}">'
                f'{"✓" if c.get("passed") else "!"}</span></td></tr>'
                for c in checks
            )
            st.markdown(
                f"""
                <table class="checks-table">
                <thead><tr><th>검증 항목</th><th class="check-col">통과 여부</th></tr></thead>
                <tbody>{rows}</tbody>
                </table>
                """,
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)
    reproducibility = report.get("reproducibility", {})
    governance = report.get("governance", {})
    methodology_ref = reproducibility.get("methodology_ref")
    methodology_ref_text = (
        ", ".join(str(ref) for ref in methodology_ref)
        if isinstance(methodology_ref, list)
        else str(methodology_ref or "")
    )
    ips_extraction = reproducibility.get("ips_extraction") or {}
    ips_extraction_text = (
        f"모델={ips_extraction.get('model')}, 시드={ips_extraction.get('seed')}, "
        f"프롬프트 해시={ips_extraction.get('prompt_hash')}"
        if ips_extraction
        else "-"
    )
    audit_rows = [
        ("계산 해시", reproducibility.get("computation_hash")),
        ("설정 해시", reproducibility.get("config_hash")),
        ("승인 해시", reproducibility.get("approval_hash")),
        ("방법론 문서", methodology_ref_text),
        ("IPS 추출 정보", ips_extraction_text),
        ("추적 ID", reproducibility.get("trace_id")),
    ]
    audit_rows_html = "".join(
        f'<tr><td>{html.escape(str(label))}</td>'
        f'<td><span class="mono2">{html.escape(str(value or "-"))}</span></td></tr>'
        for label, value in audit_rows
    )
    raw_trace_urls = governance.get("langsmith_trace_urls")
    trace_urls = raw_trace_urls if isinstance(raw_trace_urls, dict) else {}
    valid_trace_urls = [
        (phase, url)
        for phase, url in trace_urls.items()
        if isinstance(url, str) and url.startswith("https://")
    ]
    phase_labels = {"input": "입력·IPS", "analysis": "리스크·Judge"}
    if not valid_trace_urls:
        trace_url = governance.get("langsmith_trace_url")
        if isinstance(trace_url, str) and trace_url.startswith("https://"):
            valid_trace_urls = [("single", trace_url)]
            phase_labels["single"] = "trace 열기"
    _trace_links_html = "".join(
        f'<a class="audit-link" href="{html.escape(url)}" target="_blank" rel="noopener">'
        f'LangSmith {html.escape(phase_labels.get(phase, phase))} trace</a>'
        if phase != "single"
        else f'<a class="audit-link" href="{html.escape(url)}" target="_blank" rel="noopener">'
        "LangSmith trace 열기</a>"
        for phase, url in valid_trace_urls
    )
    st.markdown(
        f"""
        <div class="audit-box">
        <div class="audit-head">
        <div class="audit-title">재현성 정보</div>
        <div class="audit-links">{_trace_links_html}</div>
        </div>
        <table>{audit_rows_html}</table>
        <div class="audit-divider"></div>
        <div class="audit-disclaimer">{report.get("disclaimer", "")}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
