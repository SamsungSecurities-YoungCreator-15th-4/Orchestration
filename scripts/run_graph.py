"""CLI 진입점 — 그래프 실행/HITL 재개/분기·루프 시연.

사용 예:
  python scripts/run_graph.py --auto-approve
  python scripts/run_graph.py --auto-approve --force-judge-fail 2
  python scripts/run_graph.py --auto-approve --with-conflict
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

THREAD_ID = "demo-thread-001"


def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def _stream_and_collect(graph, payload, config, order: list[str]) -> None:
    """그래프를 스트리밍 실행하며 노드 실행 순서를 기록."""
    for update in graph.stream(payload, config, stream_mode="updates"):
        for node_name in update:
            if node_name == "__interrupt__":
                continue
            order.append(node_name)
            print(f"  ▶ 노드 실행: {node_name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="리스크 리포트 그래프 실행")
    parser.add_argument("--auto-approve", action="store_true",
                        help="승인 게이트에서 자동 승인 후 재개")
    parser.add_argument("--force-judge-fail", type=int, default=0, metavar="N",
                        help="judge를 N회 강제 실패시켜 재작성 루프 시연")
    parser.add_argument("--with-conflict", action="store_true",
                        help="유동성 요구를 과대 설정해 충돌 분기 시연")
    args = parser.parse_args()

    os.environ["RISK_FORCE_JUDGE_FAIL"] = str(args.force_judge_fail)
    os.environ["RISK_FORCE_CONFLICT"] = "1" if args.with_conflict else "0"

    from app.graph import build_graph

    graph = build_graph()
    config = {"configurable": {"thread_id": THREAD_ID}}
    order: list[str] = []

    _print_header("1) 그래프 실행 시작")
    _stream_and_collect(graph, {}, config, order)

    snapshot = graph.get_state(config)
    if snapshot.next and "approval_gate" in snapshot.next:
        _print_header("2) 승인 대기 (HITL 인터럽트)")
        conflicts = snapshot.values.get("conflicts", [])
        print("  상태: approval_gate 직전에서 정지")
        print(f"  미해결 충돌: {len(conflicts)}건")
        for c in conflicts:
            print(f"    - {c['detail']}")

        if not args.auto_approve:
            print("\n  --auto-approve 미지정: 승인 대기 상태로 종료합니다.")
            return

        graph.update_state(
            config,
            {"approval": {"status": "approved", "approver": "cli-auto", "note": "CLI 자동 승인"}},
        )
        print("  ✔ 자동 승인 주입 → 그래프 재개")
        _stream_and_collect(graph, None, config, order)

    final = graph.get_state(config).values

    _print_header("3) 노드 실행 순서")
    print("  " + " → ".join(order))

    _print_header("4) 분기/루프 발생 내역")
    n_extract = order.count("extract_ips")
    n_rag = order.count("rag_cite")
    print(f"  충돌 재추출(분기 ①): {n_extract - 1}회 (extract_ips 총 {n_extract}회 실행)")
    print("  HITL 인터럽트(②): approval_gate 직전 정지 1회 발생")
    print(f"  judge 재작성 루프(분기 ③): {n_rag - 1}회 (judge_retries={final.get('judge_retries')})")

    _print_header("5) 최종 요약")
    print("  [metrics]")
    print(json.dumps(final.get("metrics", {}), ensure_ascii=False, indent=2))
    print("\n  [judge]")
    print(json.dumps(final.get("judge", {}), ensure_ascii=False, indent=2))
    print("\n  [report — reproducibility]")
    print(json.dumps(final.get("report", {}).get("reproducibility", {}), ensure_ascii=False, indent=2))
    print(f"\n  trace_id: {final.get('trace_id')}")
    print(f"  report title: {final.get('report', {}).get('title')}")


if __name__ == "__main__":
    main()
