"""judge_eval·assemble_report B 파트 단위 테스트."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph import MAX_JUDGE_RETRIES, route_after_judge
from app.nodes.assemble_report import assemble_report
from app.nodes.judge_eval import judge_eval


BASE_STATE = {
    "run_config": {
        "as_of_date": "2026-06-30",
        "config_hash": "config-hash",
    },
    "trace_id": "run-config-hash",
    "raw_input": "고객 입력",
    "portfolio": [
        {"asset_class": "domestic_equity", "value_krw": 1000, "weight": 0.5},
        {"asset_class": "cash", "value_krw": 1000, "weight": 0.5},
    ],
    "ips": {"risk_tolerance": "neutral"},
    "approval": {"status": "locked"},
    "metrics": {
        "confidence": 0.99,
        "horizons": {
            "1d": {"var_krw": 10, "cvar_krw": 12},
            "10d": {"var_krw": 31, "cvar_krw": 38},
        },
        "stress": {
            "scenario": "high_rate_strong_usd",
            "loss_krw": -100,
            "loss_pct": -0.05,
        },
        "meta": {
            "method": "historical",
            "n_observations": 250,
            "computation_hash": "metric-hash",
        },
    },
    "explanations": [
        {
            "topic": "VaR 해석",
            "text": (
                "기준일 2026-06-30의 VaR 설명입니다. 과거 데이터 기반 추정치이며 "
                "투자 권유가 아니고 원금 또는 수익을 보장하지 않습니다."
            ),
            "revision": 0,
        },
        {"topic": "스트레스 시나리오", "text": "스트레스 설명", "revision": 0},
    ],
    "citations": [
        {
            "claim": "VaR 설명",
            "quote": "근거 문장",
            "source": "doc.pdf",
            "chunk_id": "doc.pdf::0001",
            "verified": True,
            "extra": {"chunk_text": "VaR 설명과 스트레스 설명의 근거 문장"},
        }
    ],
}


class _PassingJudgeLLM:
    def invoke(self, prompt: str):
        axis = "hallucination" if "판정 축: hallucination" in prompt else "false_precision"
        return json.dumps(
            {"passed": True, "reason": f"{axis} 검증 통과"},
            ensure_ascii=False,
        )


def _judge(state: dict) -> dict:
    return judge_eval(state, llm=_PassingJudgeLLM())


def test_judge_passes_required_checks_with_verified_citation(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_JUDGE_FAIL", raising=False)
    out = _judge(BASE_STATE)

    assert out["judge_retries"] == 1
    assert out["judge"]["passed"] is True
    assert out["judge"]["score"] == 1.0
    assert out["judge_feedback"] == ""
    assert all(check["passed"] for check in out["judge"]["checks"])
    details = {check["name"]: check["detail"] for check in out["judge"]["checks"]}
    assert details["verified_citations_present"] == "인용 구조 확인: 검증 통과 인용 1건"
    assert details["source_validity"] == "출처 정책 게이트 충족: 검증 통과 인용 1건"


def test_judge_fails_when_computation_hash_missing(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_JUDGE_FAIL", raising=False)
    state = {**BASE_STATE, "metrics": {**BASE_STATE["metrics"], "meta": {}}}

    out = _judge(state)

    assert out["judge"]["passed"] is False
    assert "computation_hash" in out["judge"]["reason"]
    feedback = json.loads(out["judge_feedback"])
    assert feedback["failed_axes"][0]["axis"] == "computation_hash_present"


def test_judge_rejects_unverified_citation(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_JUDGE_FAIL", raising=False)
    state = {
        **BASE_STATE,
        "citations": [
            {"quote": "근거 문장", "source": "doc.pdf", "chunk_id": "doc.pdf::0001", "verified": False}
        ],
    }

    out = _judge(state)

    assert out["judge"]["passed"] is False
    assert "인용" in out["judge"]["reason"]


def test_judge_force_fail_env_still_demonstrates_loop(monkeypatch):
    monkeypatch.setenv("RISK_FORCE_JUDGE_FAIL", "1")

    first = _judge(BASE_STATE)
    second = _judge({**BASE_STATE, "judge_retries": 1})

    assert first["judge"]["passed"] is False
    assert json.loads(first["judge_feedback"])["failed_axes"][0]["axis"] == "forced_failure"
    assert second["judge"]["passed"] is True


def test_judge_force_fail_isolated_in_state(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_JUDGE_FAIL", raising=False)
    state = {**BASE_STATE, "demo_options": {"force_judge_fail": 1}}

    first = _judge(state)
    second = _judge({**state, "judge_retries": 1})

    assert first["judge"]["passed"] is False
    assert json.loads(first["judge_feedback"])["failed_axes"][0]["axis"] == "forced_failure"
    assert second["judge"]["passed"] is True


def test_judge_empty_citations_passes_with_manual_review_flag(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_JUDGE_FAIL", raising=False)
    out = _judge({**BASE_STATE, "citations": []})

    assert out["judge"]["passed"] is True
    assert out["judge"]["score"] < 1.0
    assert out["judge"]["manual_review_flags"] == [
        "인용 구조 확인: 검증 통과 인용 0건"
    ]


def test_judge_strict_citation_gate_rejects_empty_citations(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_JUDGE_FAIL", raising=False)
    state = {
        **BASE_STATE,
        "run_config": {
            **BASE_STATE["run_config"],
            "strict_citation_gate": True,
        },
        "citations": [],
    }

    out = _judge(state)
    citation_check = next(
        check
        for check in out["judge"]["checks"]
        if check["name"] == "verified_citations_present"
    )

    assert out["judge"]["passed"] is False
    assert citation_check["required"] is True
    assert "검증 통과 인용 0건" in out["judge"]["reason"]
    assert out["judge"]["manual_review_flags"] == []
    assert json.loads(out["judge_feedback"])["failed_axes"]
    report = assemble_report({**state, **out})["report"]
    assert report["governance"]["strict_citation_gate"] is True
    assert report["governance"]["manual_review_required"] is True


def test_assemble_report_adds_summary_evidence_governance():
    judged = _judge(BASE_STATE)
    state = {**BASE_STATE, **judged}

    report = assemble_report(state)["report"]

    assert report["title"] == "재현가능·설명가능 리스크 리포트"
    assert report["summary"]["portfolio"]["total_value_krw"] == 2000
    assert report["summary"]["risk"]["var_1d_krw"] == 10
    assert report["summary"]["risk"]["stress_loss_krw"] == -100
    assert report["summary"]["risk"]["stress_scenario_count"] == 1
    assert report["evidence"] == {
        "citation_count": 1,
        "verified_citation_count": 1,
        "sources": ["doc.pdf"],
        "coverage": "verified",
    }
    assert report["governance"]["judge_passed"] is True
    assert report["governance"]["strict_citation_gate"] is False
    assert report["governance"]["manual_review_required"] is False
    assert report["reproducibility"]["computation_hash"] == "metric-hash"


def test_assemble_report_shows_confidence_interval_when_engine_provides_it():
    """PR #25(엔진 신뢰구간) 배선 — confidence_interval이 있으면 범위 필드를 채운다."""
    state = {
        **BASE_STATE,
        "metrics": {
            **BASE_STATE["metrics"],
            "confidence_interval": {
                "ci_level": 0.90,
                "1d": {
                    "var_krw_low": 8, "var_krw_high": 12,
                    "cvar_krw_low": 10, "cvar_krw_high": 14,
                },
                "10d": {
                    "var_krw_low": 25, "var_krw_high": 35,
                    "cvar_krw_low": 30, "cvar_krw_high": 42,
                },
            },
        },
    }

    report = assemble_report(state)["report"]
    risk = report["summary"]["risk"]

    assert risk["ci_level"] == 0.90
    assert (risk["var_1d_krw_low"], risk["var_1d_krw_high"]) == (8, 12)
    assert (risk["cvar_10d_krw_low"], risk["cvar_10d_krw_high"]) == (30, 42)


def test_assemble_report_confidence_interval_absent_falls_back_to_point_estimate():
    """엔진이 아직 confidence_interval을 안 주는 구버전 metrics에서도 안 깨진다."""
    report = assemble_report(BASE_STATE)["report"]
    risk = report["summary"]["risk"]

    assert risk["ci_level"] is None
    assert risk["var_1d_krw_low"] is None
    assert risk["var_1d_krw"] == 10
    assert risk["var_1d_pct_low"] is None
    assert risk["var_1d_pct"] is None


def test_assemble_report_summarizes_multiple_stress_scenarios_deterministically():
    state = {
        **BASE_STATE,
        "metrics": {
            **BASE_STATE["metrics"],
            "stress": {
                "B_strong_usd": {
                    "scenario": "B_strong_usd",
                    "description": "강달러 충격",
                    "reference": "강달러 근거",
                    "loss_krw": 212_500_000.0,
                    "loss_pct": 0.0425,
                },
                "A_high_rate": {
                    "scenario": "A_high_rate",
                    "description": "고금리 충격",
                    "reference": "고금리 근거",
                    "loss_krw": 370_000_000.0,
                    "loss_pct": 0.074,
                },
            },
            "meta": {
                **BASE_STATE["metrics"]["meta"],
                "methodology_ref": "methodology_var_cvar_2026",
            },
        },
    }

    report = assemble_report(state)["report"]
    risk = report["summary"]["risk"]

    assert risk["stress_scenario"] == "A_high_rate"
    assert risk["stress_loss_krw"] == 370_000_000.0
    assert risk["stress_loss_pct"] == 0.074
    assert risk["stress_scenario_count"] == 2
    assert [item["scenario"] for item in risk["stress_scenarios"]] == [
        "A_high_rate",
        "B_strong_usd",
    ]
    assert risk["stress_scenarios"][0]["reference"] == "고금리 근거"
    assert report["reproducibility"]["methodology_ref"] == [
        "methodology_var_cvar_2026"
    ]


def test_assemble_report_collects_only_verified_cited_methodologies():
    state = {
        **BASE_STATE,
        "metrics": {
            **BASE_STATE["metrics"],
            "meta": {
                **BASE_STATE["metrics"]["meta"],
                "methodology_ref": "methodology_var_cvar_2026",
            },
        },
        "citations": [
            {
                "source": "methodology_var_cvar_2026.pdf",
                "verified": True,
                "extra": {"category": "methodology"},
            },
            {
                "source": "methodology_stress_2026.pdf",
                "verified": True,
                "extra": "malformed-extra",
            },
            {
                "source": "methodology_unverified_2026.pdf",
                "verified": False,
                "extra": {"category": "methodology"},
            },
            {"source": "house_view.pdf", "verified": True},
        ],
    }

    report = assemble_report(state)["report"]

    assert report["reproducibility"]["methodology_ref"] == [
        "methodology_stress_2026",
        "methodology_var_cvar_2026",
    ]


def test_assemble_report_portfolio_summary_is_defensive():
    state = {
        **BASE_STATE,
        "portfolio": [
            {"asset_class": "cash", "value_krw": None, "weight": 0.1},
            {"asset_class": "bond", "value_krw": 1500, "weight": None},
            "malformed",
        ],
    }

    report = assemble_report(state)["report"]

    assert report["summary"]["portfolio"]["total_value_krw"] == 1500
    assert report["summary"]["portfolio"]["asset_count"] == 3
    assert report["summary"]["portfolio"]["weights"] == {"cash": 0.1, "bond": None}


def test_assemble_report_warns_when_judge_failed_or_citations_missing(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_JUDGE_FAIL", raising=False)
    state = {
        **BASE_STATE,
        "citations": [],
        "judge": {"passed": False, "manual_review_flags": ["수동 확인"]},
        "judge_retries": MAX_JUDGE_RETRIES,
    }

    report = assemble_report(state)["report"]

    assert report["evidence"]["coverage"] == "not_available"
    assert report["governance"]["manual_review_required"] is True
    assert "judge 품질 점검이 통과되지 않았습니다." in report["warnings"]
    assert "검증 통과 인용이 없어 사람 검토가 필요합니다." in report["warnings"]
    assert "수동 확인" in report["warnings"]


def test_judge_retry_limit_routes_to_report_with_manual_review_warning():
    failed_state = {
        **BASE_STATE,
        "judge": {"passed": False, "manual_review_flags": []},
    }

    assert (
        route_after_judge(
            {**failed_state, "judge_retries": MAX_JUDGE_RETRIES - 1}
        )
        == "rag_cite"
    )
    exhausted_state = {
        **failed_state,
        "judge_retries": MAX_JUDGE_RETRIES,
    }
    assert route_after_judge(exhausted_state) == "assemble_report"

    report = assemble_report(exhausted_state)["report"]
    assert report["governance"]["judge_retries"] == MAX_JUDGE_RETRIES
    assert report["governance"]["manual_review_required"] is True
    assert "judge 품질 점검이 통과되지 않았습니다." in report["warnings"]


def test_no_force_fail_env_leaked(monkeypatch):
    monkeypatch.delenv("RISK_FORCE_JUDGE_FAIL", raising=False)
    assert os.environ.get("RISK_FORCE_JUDGE_FAIL") is None


def test_assemble_report_is_deterministic_for_same_state():
    """같은 State를 반복 실행해도 완전히 동일한 리포트가 나와야 한다(재현성 원칙)."""
    judged = _judge(BASE_STATE)
    state = {**BASE_STATE, **judged}

    report_first = assemble_report(state)["report"]
    report_second = assemble_report(state)["report"]

    assert report_first == report_second
