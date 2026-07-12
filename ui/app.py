"""자연어 IPS·포트폴리오 입력부터 PB 승인·리스크 결과까지 제공하는 Streamlit UI."""
import os
import sys
import uuid
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph import build_graph
from app.nodes.load_inputs import (
    ASSET_DEFINITIONS,
    DUMMY_PORTFOLIO,
    SAMPLE_RAW_INPUT,
    portfolio_from_percentages,
)
from app.state import (
    FIXED_AGE,
    FIXED_ASSET_EOK,
    FIXED_GOAL,
    FIXED_RISK,
    IPSProfile,
)

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


def format_krw(val, suffix: str = "원") -> str:
    if val is None:
        return "-"
    return f"{val:,.0f}{suffix}"


def format_pct(val) -> str:
    if val is None:
        return "-"
    return f"{val:.1%}"


DEFAULT_PERCENTAGES = {
    item["asset_class"]: item["weight"] * 100 for item in DUMMY_PORTFOLIO
}

with st.sidebar:
    st.header("시연 옵션")
    force_judge_fail = st.number_input(
        "judge 강제 실패 횟수", min_value=0, max_value=5, value=0
    )
    with_conflict = st.checkbox("IPS 충돌 강제 시연")
    if st.button("새 상담 시작"):
        for key in ("pending_graph", "pending_config", "pending_state", "report"):
            st.session_state.pop(key, None)
        st.rerun()

report = st.session_state.get("report")

if not report:
    st.markdown(
        """
        <div class="report-header">
        <h1>고객 상담 및 제안 포트폴리오 입력</h1>
        <p>자연어 상담에서 IPS를 추출하고 PB 승인 후에만 리스크 연산을 실행합니다.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("client_input"):
        section_title("1. 고객 자연어 상담")
        raw_input = st.text_area(
            "상담 내용",
            value=SAMPLE_RAW_INPUT,
            height=150,
            help="이름·직업·목표 수익 금액·투자기간·세금·유동성·법적 제약 등을 자유롭게 입력하세요.",
        )
        fixed_cols = st.columns(4)
        fixed_cols[0].text_input("Age", value=FIXED_AGE, disabled=True)
        fixed_cols[1].text_input("Asset (억 원)", value=f"{FIXED_ASSET_EOK:g}", disabled=True)
        fixed_cols[2].text_input("Risk", value=FIXED_RISK, disabled=True)
        fixed_cols[3].text_input("Goal", value=FIXED_GOAL, disabled=True)

        section_title("2. 제안 포트폴리오 비중")
        st.caption("6개 자산군 비중을 퍼센트 단위로 입력하세요. 합계는 100%여야 합니다.")
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
        prepare_clicked = st.form_submit_button("IPS 추출 및 PB 검토 요청", type="primary")

    if prepare_clicked:
        try:
            portfolio = portfolio_from_percentages(percentages)
            os.environ["RISK_FORCE_JUDGE_FAIL"] = str(force_judge_fail)
            os.environ["RISK_FORCE_CONFLICT"] = "1" if with_conflict else "0"
            graph = build_graph()
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            with st.spinner("gpt-4o로 IPS를 추출하고 충돌을 검사하는 중..."):
                for _ in graph.stream(
                    {"raw_input": raw_input, "portfolio": portfolio},
                    config,
                    stream_mode="updates",
                ):
                    pass
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
        section_title("3. 추출 IPS 및 PB 승인")
        st.json(pending.get("ips") or {})
        st.dataframe(pending.get("portfolio") or [], width="stretch", hide_index=True)

        conflicts = pending.get("conflicts") or []
        if conflicts:
            st.error("IPS 상충 조건이 발견되어 계산 단계로 진행할 수 없습니다.")
            st.dataframe(conflicts, width="stretch", hide_index=True)
        else:
            with st.form("pb_approval"):
                ips = pending.get("ips") or {}
                unique_text = st.text_input(
                    "Unique 수정",
                    value=ips.get("Unique", ""),
                    help="고금리·강달러 충격 문구는 저장 시 항상 맨 앞에 유지됩니다.",
                )
                approver = st.text_input("PB 승인자", placeholder="PB 이름 또는 사번")
                note = st.text_area("승인 의견", placeholder="검토 의견을 입력하세요.")
                approve_clicked = st.form_submit_button("PB 승인 후 리스크 분석", type="primary")

            if approve_clicked:
                if not approver.strip():
                    st.error("PB 승인자를 입력해야 합니다.")
                else:
                    graph = st.session_state["pending_graph"]
                    config = st.session_state["pending_config"]
                    reviewed_ips = IPSProfile.model_validate(
                        {**ips, "Unique": unique_text}
                    ).model_dump()
                    graph.update_state(
                        config,
                        {
                            "ips": reviewed_ips,
                            "approval": {
                                "status": "approved",
                                "approver": approver.strip(),
                                "note": note.strip(),
                            },
                        },
                    )
                    with st.spinner("승인된 포트폴리오의 리스크를 분석하는 중..."):
                        for _ in graph.stream(None, config, stream_mode="updates"):
                            pass
                    st.session_state["report"] = graph.get_state(config).values.get("report")
                    for key in ("pending_graph", "pending_config", "pending_state"):
                        st.session_state.pop(key, None)
                    st.rerun()

report = st.session_state.get("report")

if not report:
    st.stop()
else:
    total_value = report.get("summary", {}).get("portfolio", {}).get("total_value_krw")
    st.markdown(
        f"""
        <div class="report-header">
        <h1>{report.get("title", "재현가능·설명가능 리스크 리포트")}</h1>
        <p>기준일 {report.get("as_of_date") or "-"} · 포트폴리오 총액 {format_krw(total_value)}</p>
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
                    format_krw(risk.get('var_1d_krw')),
                    format_krw(risk.get('var_10d_krw')),
                ],
                "CVaR": [
                    format_krw(risk.get('cvar_1d_krw')),
                    format_krw(risk.get('cvar_10d_krw')),
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
            f"{format_krw(risk.get('stress_loss_krw'))}</span>",
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
                        "손실액(원)": format_krw(sc.get('loss_krw'), suffix=""),
                        "손실률": format_pct(sc.get('loss_pct')),
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
