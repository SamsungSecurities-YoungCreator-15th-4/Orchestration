"""재현가능·설명가능 리스크 리포트 엔진 — Streamlit 뷰어.

scripts/run_graph.py와 동일한 방식(build_graph → 스트리밍 실행 → HITL 자동 승인)으로
그래프를 돌리고, assemble_report가 만든 최종 report를 화면에 그린다.
"""
import os
import sys
import uuid
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph import build_graph

st.set_page_config(page_title="재현가능·설명가능 리스크 리포트 엔진", layout="wide")
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

    .footer-box {
        background: #f6f7f9; border-radius: 10px; padding: 1rem 1.2rem;
        font-size: 0.82rem; color: #555;
    }
    .footer-box .mono {
        font-family: "SFMono-Regular", Consolas, monospace;
        color: #333; font-size: 0.8rem; line-height: 1.6;
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
}


def scenario_label(code: str | None) -> str:
    if not code:
        return "-"
    return SCENARIO_LABELS.get(code, code)


with st.sidebar:
    st.header("실행 옵션")
    force_judge_fail = st.number_input("judge 강제 실패 횟수", min_value=0, max_value=5, value=0)
    with_conflict = st.checkbox("IPS 충돌 시연")
    run_clicked = st.button("그래프 실행", type="primary")

if run_clicked:
    os.environ["RISK_FORCE_JUDGE_FAIL"] = str(force_judge_fail)
    os.environ["RISK_FORCE_CONFLICT"] = "1" if with_conflict else "0"

    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    with st.spinner("그래프 실행 중..."):
        for _ in graph.stream({}, config, stream_mode="updates"):
            pass

        snapshot = graph.get_state(config)
        if snapshot.next and "approval_gate" in snapshot.next:
            graph.update_state(
                config,
                {"approval": {"status": "approved", "approver": "ui-auto", "note": "UI 자동 승인"}},
            )
            for _ in graph.stream(None, config, stream_mode="updates"):
                pass

    st.session_state["report"] = graph.get_state(config).values.get("report")

report = st.session_state.get("report")

if not report:
    st.markdown(
        """
        <div class="report-header">
        <h1>재현가능·설명가능 리스크 리포트 엔진</h1>
        <p>왼쪽에서 옵션을 선택하고 '그래프 실행'을 눌러 리포트를 생성하세요.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    total_value = report.get("summary", {}).get("portfolio", {}).get("total_value_krw")
    st.markdown(
        f"""
        <div class="report-header">
        <h1>{report.get("title", "재현가능·설명가능 리스크 리포트")}</h1>
        <p>기준일 {report.get("as_of_date") or "-"} · 포트폴리오 총액 {f"{total_value:,.0f}" if total_value else "-"}원</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    warnings = report.get("warnings") or []
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        st.markdown(
            f'<div class="notice-box"><strong>확인 필요</strong><ul style="margin:0.4rem 0 0 1.1rem;">{items}</ul></div>',
            unsafe_allow_html=True,
        )

    risk = report.get("summary", {}).get("risk", {})
    with st.container(border=True):
        section_title("핵심 지표 (VaR / CVaR, 99% 신뢰수준)")
        st.table(
            {
                "기간": ["1일", "10일"],
                "VaR": [
                    f"{risk.get('var_1d_krw', 0):,.0f}원",
                    f"{risk.get('var_10d_krw', 0):,.0f}원",
                ],
                "CVaR": [
                    f"{risk.get('cvar_1d_krw', 0):,.0f}원",
                    f"{risk.get('cvar_10d_krw', 0):,.0f}원",
                ],
            }
        )

    with st.container(border=True):
        section_title("스트레스 테스트")
        scenario_count = risk.get("stress_scenario_count", 0)
        st.caption(f"최악 시나리오 기준 (전체 {scenario_count}건 중 최대 손실)")
        s1, s2 = st.columns(2)
        s1.metric("대표 시나리오", scenario_label(risk.get("stress_scenario")))
        s2.markdown(
            f"**손실액**<br><span style='color:#0b4fbf; font-size:1.3rem; font-weight:700;'>"
            f"{risk.get('stress_loss_krw', 0):,.0f}원</span>",
            unsafe_allow_html=True,
        )
        scenarios = risk.get("stress_scenarios") or []
        if scenarios:
            st.table(
                [
                    {
                        "시나리오": scenario_label(sc.get("scenario")),
                        "설명": sc.get("description"),
                        "근거": sc.get("reference"),
                        "손실액(원)": f"{sc.get('loss_krw', 0):,.0f}",
                        "손실률": f"{sc.get('loss_pct', 0):.1%}",
                    }
                    for sc in scenarios
                ]
            )

    with st.container(border=True):
        section_title("근거 (RAG 인용)")
        evidence = report.get("evidence", {})
        e1, e2 = st.columns(2)
        e1.metric("검증 통과 인용", f"{evidence.get('verified_citation_count', 0)}건")
        e2.metric("전체 인용", f"{evidence.get('citation_count', 0)}건")
        if evidence.get("sources"):
            st.caption("출처: " + ", ".join(evidence["sources"]))

    with st.container(border=True):
        section_title("품질 검증")
        governance = report.get("governance", {})
        judge = report.get("judge", {})
        judge_passed = governance.get("judge_passed")
        gate_on = governance.get("strict_citation_gate")

        def status_tile(label: str, value: str, is_positive: bool) -> str:
            tone = "status-tile-blue" if is_positive else "status-tile-gray"
            return (
                f'<div class="status-tile {tone}">'
                f'<div class="label">{label}</div><div class="value">{value}</div></div>'
            )

        t1, t2 = st.columns(2)
        t1.markdown(
            status_tile("품질 검증", "통과" if judge_passed else "검토 필요", bool(judge_passed)),
            unsafe_allow_html=True,
        )
        t2.markdown(
            status_tile("근거 검증 방식", "엄격" if gate_on else "표준", bool(gate_on)),
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)
        checks = judge.get("checks") or []
        if checks:
            st.dataframe(
                [
                    {"검증 항목": c.get("detail"), "통과 여부": c.get("passed")}
                    for c in checks
                ],
                width="stretch",
                hide_index=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)
    reproducibility = report.get("reproducibility", {})
    st.markdown(
        f"""
        <div class="footer-box">
        {report.get("disclaimer", "")}
        <br><br>
        <div class="mono">
        computation_hash: {reproducibility.get('computation_hash')}<br>
        methodology_ref:&nbsp;&nbsp;{reproducibility.get('methodology_ref')}<br>
        trace_id:&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{reproducibility.get('trace_id')}
        </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
