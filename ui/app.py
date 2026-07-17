"""자연어 IPS·포트폴리오 입력부터 PB 승인·리스크 결과까지 제공하는 Streamlit UI."""
import html
import sys
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph import build_graph
from app.nodes.load_inputs import (
    ASSET_DEFINITIONS,
    DUMMY_PORTFOLIO,
    SAMPLE_RAW_INPUT,
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

st.markdown(
    """
    <style>
    html, body, [class*="css"] { font-size: 15px; }
    [data-testid="stMetricValue"] { font-size: 1.3rem; }
    [data-testid="stMetricLabel"] { font-size: 0.85rem; }
    table { font-size: 0.9rem; }

    .report-header {
        background: linear-gradient(135deg, #0b3d91 0%, #1f6feb 100%);
        border-radius: 14px;
        padding: 1.6rem 2rem;
        color: white;
        margin-bottom: 1.2rem;
    }
    .report-header h1 { color: white; margin: 0 0 0.3rem 0; font-size: 1.4rem; }
    .report-header p { color: #dbe7ff; margin: 0; font-size: 0.9rem; }

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
    .citation-table col.col-topic { width: 15%; }
    .citation-table col.col-quote { width: 45%; }
    .citation-table col.col-source { width: 25%; }
    .citation-table col.col-date { width: 15%; }

    @media print {
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
        .footer-box, .checks-table tr, .basis-table tr, .citation-table tr {
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

LOGO_MARK_SVG = (
    '<svg viewBox="0 0 120 120" width="34" height="34" '
    'fill="none" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M92,26 C70,8 45,10 42,28 C39,46 62,46 66,60 '
    'C70,76 50,86 30,78 C18,73 12,66 10,58" '
    'stroke="#1B3B8F" stroke-width="9"/>'
    '<path d="M86,34 C68,22 50,26 50,38 C50,50 66,50 68,60 '
    'C70,72 54,78 40,72" '
    'stroke="#4D7FE0" stroke-width="9"/>'
    '<path d="M88,22 L98,14 L92,26 Z" fill="#D9B98A"/>'
    "</svg>"
)

WARNING_ICON_SVG = (
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" '
    'stroke="currentColor" stroke-width="1.6" style="vertical-align:-2px;margin-right:0.3rem;">'
    '<path d="M8 1.5 15 14.5H1z" stroke-linejoin="round"/>'
    '<path d="M8 6v3.5" stroke-linecap="round"/><circle cx="8" cy="12" r="0.6" fill="currentColor"/>'
    "</svg>"
)

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
        """
        <div class="report-header">
        <h1>고객 상담 및 포트폴리오 입력</h1>
        <p>상담 내역에서 IPS를 추출하고 PB 승인 후에만 계산을 진행합니다.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("client_input"):
        section_title("1. 고객 상담")
        raw_input = st.text_area(
            "상담 내용",
            value=SAMPLE_RAW_INPUT,
            height=150,
        )
        fixed_cols = st.columns(5)
        fixed_cols[0].text_input("Age", value=FIXED_AGE, disabled=True)
        fixed_cols[1].text_input("Job", value=FIXED_JOB, disabled=True)
        fixed_cols[2].text_input("Asset (억 원)", value=f"{FIXED_ASSET_EOK:g}", disabled=True)
        fixed_cols[3].text_input("Risk", value=FIXED_RISK, disabled=True)
        fixed_cols[4].text_input("Goal", value=FIXED_GOAL, disabled=True)

        section_title("2. 포트폴리오 비중")
        st.caption("6개 자산군 비중을 입력해 주세요. (합계 100% 기준)")
        percentages: dict[str, float] = {}
        cols = st.columns(3)
        for idx, (asset_class, name) in enumerate(ASSET_DEFINITIONS):
            percentages[asset_class] = cols[idx % 3].number_input(
                f"{name} (%)",
                min_value=0.0,
                max_value=100.0,
                value=float(DEFAULT_PERCENTAGES[asset_class]),
                step=1.0,
            )
        total_pct = sum(percentages.values())
        st.caption(f"현재 합계: {total_pct:g}%")

        with st.expander("시연 옵션"):
            st.caption("judge 강제 실패 횟수")
            force_judge_fail = st.number_input(
                "judge 강제 실패 횟수",
                min_value=0, max_value=5, value=0,
                label_visibility="collapsed",
            )

        prepare_clicked = st.form_submit_button("IPS 추출", type="primary")

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
        section_title("3. IPS 및 PB 승인")
        ips_rows = [{"항목": key, "값": value} for key, value in (pending.get("ips") or {}).items()]
        st.dataframe(ips_rows, use_container_width=True, hide_index=True)
        portfolio_rows = [
            {
                "자산군": item.get("name"),
                "금액": format_krw(item.get("value_krw")),
                "비중": format_pct(item.get("weight")),
            }
            for item in (pending.get("portfolio") or [])
            if isinstance(item, dict)
        ]
        st.dataframe(portfolio_rows, use_container_width=True, hide_index=True)

        conflicts = pending.get("conflicts") or []
        blocking_conflicts = [
            conflict for conflict in conflicts if conflict.get("severity") == "block"
        ]
        review_conflicts = [
            conflict for conflict in conflicts if conflict.get("severity") == "review"
        ]
        if conflicts:
            if blocking_conflicts:
                st.error("예외 승인할 수 없는 IPS 충돌이 있어 입력 보완이 필요합니다.")
            else:
                st.warning("PB의 구체적 사유가 있는 예외 승인 후 리스크 계산만 진행할 수 있습니다.")
            st.dataframe(conflicts, use_container_width=True, hide_index=True)
        approve_clicked = False
        if not blocking_conflicts:
            with st.form("pb_approval"):
                ips = pending.get("ips") or {}
                unique_text = st.text_input(
                    "Unique 수정",
                    value=ips.get("Unique", ""),
                )
                approver_name = st.text_input("PB 이름", placeholder="PB 이름 입력")
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
    total_value = report.get("summary", {}).get("portfolio", {}).get("total_value_krw")
    st.markdown(
        f"""
        <div class="brand-header">
        <div class="brand-row">{LOGO_MARK_SVG}<span class="wordmark">S<span class="dot">.</span>ymphony</span></div>
        <div class="report-subtitle">{report.get("title", "재현가능·설명가능 리스크 리포트")}</div>
        <p>기준일 {report.get("as_of_date") or "-"} · 포트폴리오 총액 {format_krw(total_value)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    warnings = report.get("warnings") or []
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        st.markdown(
            f'<div class="notice-box"><strong>{WARNING_ICON_SVG}최신 데이터 검토 필요</strong>'
            f'<ul style="margin:0.4rem 0 0 1.1rem;">{items}</ul></div>',
            unsafe_allow_html=True,
        )

    risk = report.get("summary", {}).get("risk", {})
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
            st.markdown(f"#### {section['title']}")
        if not section_citations:
            st.caption("현재 포트폴리오 조건에 해당하는 인용 정보가 없습니다.")
            return
        rows = citation_table_rows(section_citations)

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
            f"<td>{html.escape(str(row['설명주제']))}</td>"
            f"<td>{html.escape(str(row['근거문장']))}</td>"
            f"<td>{_source_cell(str(row['출처']))}</td>"
            f"<td>{html.escape(str(row['발행기준일']))}</td>"
            "</tr>"
            for row in rows
        )
        st.markdown(
            '<table class="citation-table">'
            '<colgroup>'
            '<col class="col-topic"><col class="col-quote">'
            '<col class="col-source"><col class="col-date">'
            "</colgroup>"
            "<thead><tr><th>주제</th><th>인용 문장</th>"
            "<th>출처</th><th>발행일</th></tr></thead>"
            f"<tbody>{body}</tbody></table>",
            unsafe_allow_html=True,
        )

    with st.container(border=True):
        ci_level = risk.get("ci_level")
        section_title("최대 손실 위험 지표 (VaR / CVaR, 신뢰수준 99%)")
        if ci_level is not None:
            st.caption(f"오차 범위 {ci_level:.0%} 신뢰구간 기준")
        st.table(
            {
                "기간": ["1일", "10일"],
                "VaR (금액)": [
                    format_range(
                        risk.get("var_1d_krw_low"), risk.get("var_1d_krw_high"), risk.get("var_1d_krw")
                    ),
                    format_range(
                        risk.get("var_10d_krw_low"), risk.get("var_10d_krw_high"), risk.get("var_10d_krw")
                    ),
                ],
                "VaR (수익률)": [
                    format_pct_range(
                        risk.get("var_1d_pct_low"), risk.get("var_1d_pct_high"), risk.get("var_1d_pct")
                    ),
                    format_pct_range(
                        risk.get("var_10d_pct_low"), risk.get("var_10d_pct_high"), risk.get("var_10d_pct")
                    ),
                ],
                "CVaR (금액)": [
                    format_range(
                        risk.get("cvar_1d_krw_low"), risk.get("cvar_1d_krw_high"), risk.get("cvar_1d_krw")
                    ),
                    format_range(
                        risk.get("cvar_10d_krw_low"), risk.get("cvar_10d_krw_high"), risk.get("cvar_10d_krw")
                    ),
                ],
                "CVaR (수익률)": [
                    format_pct_range(
                        risk.get("cvar_1d_pct_low"), risk.get("cvar_1d_pct_high"), risk.get("cvar_1d_pct")
                    ),
                    format_pct_range(
                        risk.get("cvar_10d_pct_low"), risk.get("cvar_10d_pct_high"), risk.get("cvar_10d_pct")
                    ),
                ],
            }
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
        st.markdown(
            '<div style="font-size:0.78rem;font-weight:700;color:#999;'
            'margin:0.6rem 0 0.2rem 0;">산출 근거</div>'
            '<table class="basis-table">'
            f'<tr><td>관측 데이터 기간</td><td>{html.escape(period_text)}</td></tr>'
            f'<tr><td>적용 환율</td><td>{html.escape(fx_rate_text)}</td></tr>'
            f'<tr><td>방법론</td><td>{html.escape(methodology_text)}</td></tr>'
            "</table>",
            unsafe_allow_html=True,
        )
        _methodology_section = next(
            s for s in RAG_EVIDENCE_SECTIONS if s["category"] == "methodology"
        )
        _render_citation_section(_methodology_section, heading_override="정량 계산 방법론")

    drilldown = risk.get("drilldown") or []
    if drilldown:
        with st.container(border=True):
            section_title("CVaR 자산군별 기여도")
            st.caption(
                "최악 1% 구간에서 각 자산군이 CVaR에 기여한 정도  \n"
                "\\+ 손실 위험 증가 / − 손실 위험 완화"
            )
            st.table(
                [
                    {
                        "자산군": ASSET_LABELS.get(row["asset_class"], row["asset_class"]),
                        "기여 금액": format_krw(row["contribution_krw"]),
                        "기여 비중": format_pct(row["contribution_pct"]),
                    }
                    for row in drilldown
                ]
            )

    with st.container(border=True):
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
            st.table(
                [
                    {
                        "시나리오": scenario_label(sc.get("scenario")),
                        "설명": sc.get("description"),
                        "근거": sc.get("reference"),
                        "손실액(범위)": format_range(
                            sc.get("loss_krw_low"), sc.get("loss_krw_high"), sc.get("loss_krw")
                        ),
                        "손실률(범위)": format_pct_range(
                            sc.get("loss_pct_low"), sc.get("loss_pct_high"), sc.get("loss_pct")
                        ),
                    }
                    for sc in scenarios
                ]
            )

    with st.container(border=True):
        section_title("분석 근거 및 원문 출처")
        evidence = report.get("evidence", {})
        e1, e2 = st.columns(2)
        e1.metric("유효한 검증 근거", f"{evidence.get('verified_citation_count', 0)}건")
        e2.metric("전체 참조 자료", f"{evidence.get('citation_count', 0)}건")

        for section in RAG_EVIDENCE_SECTIONS:
            if section["category"] == "methodology":
                continue
            _render_citation_section(section)

    with st.container(border=True):
        section_title("리포트 신뢰성 검증")
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
                f'<tr><td>{html.escape(str(c.get("detail") or ""))}</td>'
                f'<td class="check-col">{"☑" if c.get("passed") else "☐"}</td></tr>'
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
        f'<td><span class="mono">{html.escape(str(value or "-"))}</span></td></tr>'
        for label, value in audit_rows
    )
    st.markdown(
        f"""
        <div class="footer-box">
        {report.get("disclaimer", "")}
        <br><br>
        <table class="basis-table">{audit_rows_html}</table>
        </div>
        """,
        unsafe_allow_html=True,
    )
    raw_trace_urls = governance.get("langsmith_trace_urls")
    trace_urls = raw_trace_urls if isinstance(raw_trace_urls, dict) else {}
    valid_trace_urls = [
        (phase, url)
        for phase, url in trace_urls.items()
        if isinstance(url, str) and url.startswith("https://")
    ]
    if valid_trace_urls:
        columns = st.columns(len(valid_trace_urls))
        phase_labels = {"input": "입력·IPS", "analysis": "리스크·Judge"}
        for column, (phase, url) in zip(columns, valid_trace_urls, strict=True):
            column.link_button(f"LangSmith {phase_labels.get(phase, phase)} trace", url)
    else:
        trace_url = governance.get("langsmith_trace_url")
        if isinstance(trace_url, str) and trace_url.startswith("https://"):
            st.link_button("LangSmith trace 열기", trace_url)
