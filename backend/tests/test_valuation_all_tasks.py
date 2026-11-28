"""End-to-end backend test verifying ALL 8 valuation AI tasks (T1-T8) are available, executable, and served via /runs/{id}/analysis."""
from __future__ import annotations

import time

from backend.app.services import ai_tasks, valuation_ai


def test_all_eight_valuation_tasks_run_in_deep_mode_and_persist_analysis(configured, monkeypatch):
    from src.pipeline import orchestrator

    monkeypatch.setattr(ai_tasks, "_yahoo_news", lambda ticker, limit=10: [])

    # Mock LLM caller that succeeds for T4/T5/T6/T7/T8 without network
    class FakeClient:
        usage = {"events": []}

    def fake_caller(cfg, use_llm):
        if not use_llm:
            return None, None
        c = FakeClient()
        c.usage = {"events": []}

        def _call(sys_p, user_t, tag):
            c.usage["events"].append({"tag": tag, "reported": True, "prompt_tokens": 500, "completion_tokens": 200})
            if tag == "t4_scenarios":
                return {"scenarios": [
                    {"key": "bear", "probability": 0.25, "trigger": "Demand miss", "narrative": "Bear"},
                    {"key": "base", "probability": 0.50, "trigger": "Consensus", "narrative": "Base"},
                    {"key": "bull", "probability": 0.25, "trigger": "Beat", "narrative": "Bull"},
                ]}
            if tag == "t5_peers":
                return {"peers": [{"ticker": "CVX", "rationale": "Integrated energy"}, {"ticker": "XOM", "rationale": "Global oil major"}]}
            if tag == "t6_red_team":
                return {"findings": [{"severity": "medium", "topic": "oil_cycle", "detail": "Brent price cyclicality"}]}
            if tag == "t7_report":
                return {"thesis": "Solid cash generation at current oil curve.", "risks": ["Commodity volatility"]}
            if tag == "t8_segments":
                return {"segments": [{"name": "Exploration & Production", "revenue_share": 0.85, "driver": "Volume × Brent"}], "sotp_note": "E&P driven"}
            return None

        return c, _call

    monkeypatch.setattr(valuation_ai, "_make_llm_caller", fake_caller)

    def fake_run_ticker(ticker, cfg=None, use_llm=True, valuation_date="", progress=None, should_stop=None,
                        skip_extract=False, apply_adjustments=True, statements=None):
        for key in ("quote", "fundamentals", "rf", "filings", "ai_assume", "spec", "engine", "export", "state"):
            if progress:
                progress(key, f"step {key}")
        return {
            "json": {
                "ticker": ticker, "asset": "CNOOC Limited", "valuation_date": "2026-11-28", "currency": "CNY",
                "fx_if_any": {"pair": "CNY/HKD"}, "price": 22.72, "price_date": "2026-11-28",
                "fair_value_per_share": 35.0, "assumption_source": "llm",
                "discount": {"wacc": {"value": 0.085}}, "terminal": {"g": 0.025, "tv_share_of_ev": 0.55},
                "base_year": {"revenue": 398220.0, "ebit_margin": 0.43},
                "projections": [{"year": "FY2027", "revenue": 410000.0, "ebit_margin": 0.42, "da": 65000.0, "capex": 70000.0}],
                "scenarios": [{"name": "悲觀", "fair_value": 26.0}, {"name": "基準", "fair_value": 35.0}, {"name": "樂觀", "fair_value": 44.0}],
                "data_gaps": [],
                "llm_usage": {"events": [{"tag": "propose_assumptions", "reported": True, "prompt_tokens": 2000, "completion_tokens": 500}]},
            },
            "path": None, "xlsx": None, "verify_pass": True, "verify_errors": [],
        }

    monkeypatch.setattr(orchestrator, "run_ticker", fake_run_ticker)

    # 1. Plan in deep mode shows ALL 8 tasks available & will_run (ZERO next_phase!)
    p = configured.get("/api/v1/ai/plan", params={"ticker": "0883.HK", "mode": "deep"}).json()
    assert len(p["tasks"]) == 8
    assert all(t["available"] and t["will_run"] and t["skip_code"] is None for t in p["tasks"])

    # 2. Run deep valuation job → all 8 tasks execute and complete
    r = configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "deep"})
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    job = None
    for _ in range(100):
        job = configured.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert job and job["status"] == "done", job
    ai_steps = {s["key"]: s for s in job["steps"] if s["kind"] == "ai"}
    assert set(ai_steps) == {"T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"}
    assert all(s["status"] == "done" for s in ai_steps.values()), ai_steps
    assert job["cost_usd"] > 0

    # 3. Run analysis endpoint serves persisted T1, T2, T4, T5, T6, T7, T8 structured outputs
    an = configured.get(f"/api/v1/runs/{job['run_id']}/analysis")
    assert an.status_code == 200, an.text
    body = an.json()
    assert {"T1", "T2", "T4", "T5", "T6", "T7", "T8"} <= set(body["ai_analysis"])
    assert body["ai_analysis"]["T4"]["probability_weighted_fv"] == 35.0
    assert body["ai_analysis"]["T5"]["peers"][0]["ticker"] == "CVX"
    assert body["ai_analysis"]["T7"]["thesis"] == "Solid cash generation at current oil curve."
    assert len(body["tasks"]) == 8
