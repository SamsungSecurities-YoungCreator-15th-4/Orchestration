"""StateGraph 조립 — 8노드 + 조건부 엣지 3개 + HITL 인터럽트."""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.nodes.approval_gate import approval_gate
from app.nodes.assemble_report import assemble_report
from app.nodes.conflict_check import conflict_check
from app.nodes.extract_ips import extract_ips
from app.nodes.judge_eval import judge_eval
from app.nodes.load_inputs import load_inputs
from app.nodes.rag_cite import rag_cite
from app.nodes.var_engine import var_engine
from app.state import RiskState

MAX_CONFLICT_RETRIES = 1
MAX_JUDGE_RETRIES = 3


def route_after_conflict_check(state: RiskState) -> str:
    """분기 ①: 충돌이 있고 재추출 여유가 남아 있으면 extract_ips로 회귀.

    재시도 소진 시에는 충돌을 approval에 첨부한 채 사람 판단(approval_gate)으로 넘긴다.
    """
    if state.get("conflicts") and state.get("conflict_retries", 0) < MAX_CONFLICT_RETRIES:
        return "extract_ips"
    return "approval_gate"


def route_after_judge(state: RiskState) -> str:
    """분기 ③: judge 통과 또는 재시도 소진 시 리포트 조립, 아니면 재작성 루프."""
    if state.get("judge", {}).get("passed") or state.get("judge_retries", 0) >= MAX_JUDGE_RETRIES:
        return "assemble_report"
    return "rag_cite"


def build_graph():
    """컴파일된 그래프 반환. ②: approval_gate 직전 인터럽트(HITL) + MemorySaver."""
    g = StateGraph(RiskState)

    g.add_node("load_inputs", load_inputs)
    g.add_node("extract_ips", extract_ips)
    g.add_node("conflict_check", conflict_check)
    g.add_node("approval_gate", approval_gate)
    g.add_node("var_engine", var_engine)
    g.add_node("rag_cite", rag_cite)
    g.add_node("judge_eval", judge_eval)
    g.add_node("assemble_report", assemble_report)

    g.add_edge(START, "load_inputs")
    g.add_edge("load_inputs", "extract_ips")
    g.add_edge("extract_ips", "conflict_check")
    g.add_conditional_edges(
        "conflict_check",
        route_after_conflict_check,
        {"extract_ips": "extract_ips", "approval_gate": "approval_gate"},
    )
    g.add_edge("approval_gate", "var_engine")
    g.add_edge("var_engine", "rag_cite")
    g.add_edge("rag_cite", "judge_eval")
    g.add_conditional_edges(
        "judge_eval",
        route_after_judge,
        {"rag_cite": "rag_cite", "assemble_report": "assemble_report"},
    )
    g.add_edge("assemble_report", END)

    return g.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["approval_gate"],
    )
