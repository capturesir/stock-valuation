"""Unit tests for all valuation pipeline AI tasks T4, T5, T6, T7, T8 (rules + LLM paths)."""
from __future__ import annotations

from libs.sv_ai import (
    run_t4_scenarios,
    run_t5_peers,
    run_t6_red_team,
    run_t7_report,
    run_t8_segments,
)


def _sample_calc():
    return {
        "ticker": "NVDA",
        "valuation_date": "2026-11-28",
        "currency": "USD",
        "price": 146.94,
        "fair_value_per_share": 310.0,
        "discount": {"wacc": {"value": 0.105}},
        "terminal": {"g": 0.03, "tv_share_of_ev": 0.72},
        "base_year": {"revenue": 215938.0, "ebit_margin": 0.60},
        "projections": [
            {"year": "FY2027", "revenue": 320000.0, "ebit_margin": 0.58, "da": 4000.0, "capex": 6000.0},
            {"year": "FY2036", "revenue": 900000.0, "ebit_margin": 0.52, "da": 9000.0, "capex": 12000.0},
        ],
        "scenarios": [
            {"name": "悲觀", "fair_value": 210.0, "changes": "growth -3pp"},
            {"name": "基準", "fair_value": 310.0, "changes": "base"},
            {"name": "樂觀", "fair_value": 420.0, "changes": "growth +3pp"},
        ],
        "data_gaps": ["Generic spec notice"],
    }


def test_t4_scenarios_normalizes_probabilities_and_computes_weighted_fv():
    calc = _sample_calc()
    r = run_t4_scenarios("NVDA", calc)
    assert r["source"] == "rules"
    assert abs(sum(s["probability"] for s in r["scenarios"]) - 1.0) < 1e-6
    assert r["probability_weighted_fv"] == 210.0 * 0.25 + 310.0 * 0.50 + 420.0 * 0.25

    # LLM path normalizes unnormalized probabilities cleanly
    r_llm = run_t4_scenarios(
        "NVDA", calc,
        llm_chat=lambda s, u, tag: {
            "scenarios": [
                {"key": "bear", "probability": 0.3, "trigger": "AI capex pause", "narrative": "Slowdown"},
                {"key": "base", "probability": 0.5, "trigger": "Steady ramp", "narrative": "Consensus"},
                {"key": "bull", "probability": 0.4, "trigger": "Sovereign AI surge", "narrative": "Upside"},
            ]
        },
    )
    assert r_llm["source"] == "llm"
    assert abs(sum(s["probability"] for s in r_llm["scenarios"]) - 1.0) < 1e-4


def test_t5_t6_t7_t8_deterministic_outputs():
    calc = _sample_calc()
    t5 = run_t5_peers("NVDA", "Semiconductor")
    assert len(t5["peers"]) >= 4 and all(p["ticker"] != "NVDA" for p in t5["peers"])

    t6 = run_t6_red_team("NVDA", calc, {"warnings": ["methods_divergence_warning"], "reverse_dcf": {"implied_growth": 0.12, "base_growth": 0.22, "gap_pp": -10.0}})
    assert t6["risk_level"] in ("high", "medium")
    topics = {f["topic"] for f in t6["findings"]}
    assert "valuation_vs_market" in topics and "terminal_dominance" in topics and "reverse_dcf_gap" in topics

    t7 = run_t7_report(
        "NVDA", calc, {"composite_value": 260.0},
        prev_run={"valuation_date": "2026-11-11", "composite_value": 300.0, "wacc": 0.10, "terminal_g": 0.03, "price": 140.0},
    )
    assert t7["attribution"] is not None
    assert t7["attribution"]["delta"] == -40.0
    assert any("WACC" in d for d in t7["attribution"]["drivers"])

    t8 = run_t8_segments("NVDA", calc, doc_snippets=[{"title": "NVDA 10-K", "section": "Segment Note", "text": "Compute & Networking 88%"}])
    assert len(t8["segments"]) >= 1
    assert "1 filing snippet" in t8["sotp_note"]
