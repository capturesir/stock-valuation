"""v1.1 research layer: statement review, adjustments, plan, history, pages.

Nothing here touches the network or the real cases/ directory except read-only
NVDA fixtures that are committed with the repository.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.pipeline import adjustments as adj
from src.web import app as appmod
from src.web import research


@pytest.fixture()
def client():
    return TestClient(appmod.app, raise_server_exceptions=False)


@pytest.fixture()
def fake_case(tmp_path, monkeypatch):
    """A tiny cases/<ticker>/ with one annual statement, isolated per test."""
    root = tmp_path / "cases"

    def _case_dir(ticker):
        d = root / adj.norm_ticker(ticker)
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(adj, "case_dir", _case_dir)
    d = _case_dir("TEST")
    (d / "yahoo_fundamentals.json").write_text(json.dumps({
        "info": {"currency": "USD"},
        "is_a": {"2025-12-31": {"Total Revenue": 1000.0, "Operating Income": 100.0,
                                "Tax Rate For Calcs": 0.2},
                 "2024-12-31": {"Total Revenue": 900.0, "Operating Income": 90.0,
                                "Tax Rate For Calcs": 0.2}},
        "cf_a": {"2025-12-31": {"Capital Expenditure": -50.0,
                                "Depreciation And Amortization": 40.0}},
        "bs_a": {"2025-12-31": {"Ordinary Shares Number": 100_000_000.0}},
    }), encoding="utf-8")
    return d


# ------------------------------------------------------------------ pages ---

def test_settings_page_is_404_when_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "STATIC_DIR", tmp_path)
    r = client.get("/settings")
    assert r.status_code == 404
    assert "settings.html" in r.json()["detail"]["error"]


def test_settings_page_is_served_when_present(client, tmp_path, monkeypatch):
    (tmp_path / "settings.html").write_text("<html>ok</html>", encoding="utf-8")
    monkeypatch.setattr(appmod, "STATIC_DIR", tmp_path)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", "")


def test_guide_pdf_only_serves_our_own_guide(client, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "DOCS_DIR", tmp_path)
    (tmp_path / "someone-elses-guide.pdf").write_bytes(b"%PDF-1.4 x")
    assert client.get("/api/guide.pdf").status_code == 404
    (tmp_path / "使用指南-v1.1.pdf").write_bytes(b"%PDF-1.4 y")
    r = client.get("/api/guide.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"


# --------------------------------------------------------------- review ----

def test_review_lists_every_item_with_amounts_first():
    data = research.statement_rows("NVDA")
    assert data["ok"] is True
    assert data["count"] > 100  # every collected line item, not a short summary
    kinds = [r["kind"] for s in data["statements"] for r in s["rows"]]
    assert kinds[0] == "amount"
    # amounts are ordered by size, and never interleaved with ratios
    is_rows = next(s for s in data["statements"] if s["key"] == "is")["rows"]
    amounts = [r for r in is_rows if r["kind"] == "amount"]
    values = [abs(r["latest"] or 0) for r in amounts]
    assert values == sorted(values, reverse=True)
    assert all(r["kind"] == "amount" for r in is_rows[: len(amounts)])
    # the model inputs are flagged so the user can see what feeds the valuation
    flagged = {r["model_input"] for r in is_rows if r["model_input"]}
    assert "revenue" in flagged


def test_review_reports_missing_fundamentals_readably(fake_case):
    data = research.statement_rows("NOPE")
    assert data["ok"] is False
    assert "尚未收集" in data["error"]


# ---------------------------------------------------- adjustments (R06/R07) --

def test_adjustment_requires_a_reason(fake_case):
    with pytest.raises(ValueError):
        adj.add_adjustment("TEST", {"statement": "is", "item": "Total Revenue",
                                    "fy": "2025-12-31", "adjusted": 1100.0, "reason": ""})


def test_adjustment_keeps_original_and_is_recorded(fake_case):
    rec = adj.add_adjustment("TEST", {"statement": "is", "item": "Total Revenue",
                                      "fy": "2025-12-31", "adjusted": 1100.0,
                                      "reason": "年報顯示一次性收入"})
    assert rec["original"] == 1000.0
    assert rec["adjusted"] == 1100.0
    assert rec["delta"] == 100.0
    assert rec["affects_model"] is True
    assert rec["model_input"] == "revenue"
    items = adj.load_adjustments("TEST")
    assert len(items) == 1 and items[0]["reason"] == "年報顯示一次性收入"


def test_adjustment_rejects_unknown_item(fake_case):
    with pytest.raises(ValueError):
        adj.add_adjustment("TEST", {"statement": "is", "item": "Not A Line",
                                    "fy": "2025-12-31", "adjusted": 1.0, "reason": "x"})


def test_adjustment_delete_removes_it(fake_case):
    rec = adj.add_adjustment("TEST", {"statement": "is", "item": "Total Revenue",
                                      "fy": "2025-12-31", "adjusted": 1100.0, "reason": "調整"})
    assert adj.delete_adjustment("TEST", rec["id"]) is True
    assert adj.load_adjustments("TEST") == []
    assert adj.delete_adjustment("TEST", "missing") is False


def test_apply_to_spec_scales_every_revenue_anchor(fake_case):
    adj.add_adjustment("TEST", {"statement": "is", "item": "Total Revenue",
                                "fy": "2025-12-31", "adjusted": 1100.0, "reason": "調整"})
    spec = {
        "prev_revenue_for_first": 900.0, "first_revenue": 1000.0,
        "margins": [0.1, 0.1], "da_pcts": [0.04, 0.04], "capex_pcts": [0.05, 0.05],
        "shares_m": 100.0, "base_year": {"revenue": 1000.0, "ebit": 100.0,
                                         "da": 40.0, "capex": 50.0},
    }
    out, applied = adj.apply_to_spec(spec, "TEST")
    assert len(applied) == 1
    assert out["first_revenue"] == pytest.approx(1100.0)
    assert out["prev_revenue_for_first"] == pytest.approx(990.0)
    assert out["base_year"]["revenue"] == pytest.approx(1100.0)
    assert out["margins"] == spec["margins"]      # revenue change must not touch margins
    assert out["shares_m"] == 100.0
    # the caller's spec is untouched: applying is not a mutation in place
    assert spec["first_revenue"] == 1000.0


def test_apply_to_spec_is_a_noop_without_adjustments(fake_case):
    spec = {"first_revenue": 1000.0, "margins": [0.1], "da_pcts": [0.04],
            "capex_pcts": [0.05], "shares_m": 100.0, "base_year": {}}
    out, applied = adj.apply_to_spec(spec, "TEST")
    assert applied == []
    assert out is spec


# -------------------------------------------------------------- AI plan ----

def test_ai_plan_counts_come_from_this_ticker():
    plan = research.ai_plan("NVDA", use_llm=True)
    assert plan["totals"]["requests"] >= 1
    review_count = research.statement_rows("NVDA")["count"]
    extract = next(s for s in plan["steps"] if s["key"] == "ai_extract")
    assert extract["items"] == review_count          # not a fixed example number
    assert extract["will_run"] is True

    off = research.ai_plan("NVDA", use_llm=False)
    assert off["totals"]["requests"] == 0
    assert all(s["will_run"] is False for s in off["steps"])
    assert all(s["skip_reason"] for s in off["steps"])


def test_ai_plan_skips_assumption_step_for_a_locked_spec():
    plan = research.ai_plan("NVDA", use_llm=True)
    assume = next(s for s in plan["steps"] if s["key"] == "ai_assume")
    assert assume["will_run"] is False
    assert "內建" in assume["skip_reason"]


# --------------------------------------------------------------- history ----

def test_history_points_carry_the_price_of_that_day():
    data = research.valuation_history("NVDA")
    assert data["count"] >= 1
    for p in data["points"]:
        assert p["fair_value"] > 0
        assert p["price"] > 0
        assert p["gap"] == pytest.approx(p["fair_value"] - p["price"])
        assert p["price_date"]                  # the price then, not today's


def test_forecast_pairs_actuals_with_forecasts_and_reasons():
    data = research.forecast_block("NVDA")
    assert data["ok"] is True
    kinds = {r["kind"] for r in data["rows"]}
    assert {"actual", "forecast"} <= kinds
    assert len(data["reasons"]) == len([r for r in data["rows"] if r["kind"] == "forecast"]) + 1
    assert all(r["growth_rationale"] for r in data["reasons"][:-1])


def test_eta_says_when_there_is_no_sample():
    data = research.eta_estimate()
    assert data["sample_size"] >= 0
    if data["sample_size"] < 3:
        assert data["median_seconds"] is None or data["source"] == "history_thin"


# ------------------------------------------------------------------- api ----

def test_review_api_returns_adjustments_container(client):
    r = client.get("/api/review?ticker=NVDA")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["statements"]
    assert isinstance(body["adjustments"], list)


def test_adjustment_api_rejects_blank_reason(client):
    r = client.post("/api/review/adjustments", json={
        "ticker": "NVDA", "statement": "is", "item": "Total Revenue",
        "fy": "2026-01-31", "adjusted": 1.0, "reason": " "})
    assert r.status_code == 400
    assert "原因" in r.json()["detail"]["error"]


def test_workbook_api_keeps_seven_tabs(client):
    r = client.get("/api/workbook?ticker=NVDA&date=2026-11-11")
    assert r.status_code == 200
    body = r.json()
    from src.web.sheets import EXPECTED_SHEETS
    assert [s["name"] for s in body["workbook"]["sheets"]] == EXPECTED_SHEETS
    assert "research" in body and body["research"]["review"]["count"] > 0


# ------------------------------------------------------------- run log ------

def test_runlog_keeps_repeat_valuations_from_the_same_day(tmp_path, monkeypatch):
    """Same-day re-runs must all appear: folders overwrite, the log does not."""
    from src.pipeline import runlog

    monkeypatch.setattr(runlog, "LOG_DIR", tmp_path / "history")
    monkeypatch.setattr(runlog, "_path",
                        lambda t: runlog.LOG_DIR / f"{t}.json")
    for fair, ts in ((100.0, "2026-11-16T10:00:00"), (110.0, "2026-11-16T15:30:00")):
        runlog.append("TESTX", {"valuation_date": "2026-11-16", "run_at": ts,
                                "fair_value": fair, "price": 90.0, "currency": "USD"})

    data = research.valuation_history("TESTX")
    assert data["count"] == 2
    assert data["runlog_points"] == 2
    assert data["dense_dates"] == ["2026-11-16"]
    assert [p["same_day_index"] for p in data["points"]] == [0, 1]
    assert all(p["same_day_count"] == 2 for p in data["points"])
    assert data["points"][0]["fair_value"] == 100.0   # ordered by run time
    assert data["points"][1]["fair_value"] == 110.0


def test_runlog_and_folder_scan_do_not_double_count(tmp_path, monkeypatch):
    from src.pipeline import runlog

    monkeypatch.setattr(runlog, "LOG_DIR", tmp_path / "history")
    monkeypatch.setattr(runlog, "_path", lambda t: runlog.LOG_DIR / f"{t}.json")
    runlog.append("TESTX", {"valuation_date": "2026-11-16", "run_at": "2026-11-16T10:00:00",
                            "fair_value": 100.0, "price": 90.0})
    runlog.append("TESTX", {"valuation_date": "2026-11-16", "run_at": "2026-11-16T11:00:00",
                            "fair_value": 100.0, "price": 90.0})
    data = research.valuation_history("TESTX")
    assert data["count"] == 1        # identical numbers are one point, not two


def test_runlog_write_failure_is_not_fatal(tmp_path, monkeypatch):
    from src.pipeline import runlog

    monkeypatch.setattr(runlog, "_path", lambda t: tmp_path / "nope" / "x" / f"{t}.json")
    monkeypatch.setattr(runlog, "LOG_DIR", tmp_path / "nope" / "x")
    # LOG_DIR cannot be created because "nope" is a file, not a directory
    (tmp_path / "nope").write_text("", encoding="utf-8")
    entry = runlog.append("TESTX", {"valuation_date": "2026-11-16", "fair_value": 1.0, "price": 1.0})
    assert entry["ticker"] == "TESTX"     # returned, not raised
    assert runlog.read("TESTX") == []


# --------------------------------------------------- empty run folders ------

def test_empty_run_folder_does_not_hide_the_last_good_result(tmp_path, monkeypatch):
    """A stopped/crashed run leaves an empty date folder; it must not shadow a
    real result because the list is sorted newest-first."""
    from src.web import services

    out = tmp_path / "out"
    (out / "2026-11-11_TEST").mkdir(parents=True)
    (out / "2026-11-11_TEST" / "2026-11-11_TEST_FCFF.xlsx").write_bytes(b"x")
    (out / "2026-11-11_TEST" / "2026-11-11_TEST.json").write_text("{}", encoding="utf-8")
    (out / "2026-11-16_TEST").mkdir(parents=True)          # aborted today, no files
    monkeypatch.setattr(services, "ROOT", tmp_path)

    found = services.find_valuations("TEST")
    assert [f["valuation_date"] for f in found] == ["2026-11-11"]
    assert services.latest_valuation("TEST")["xlsx"].endswith("_FCFF.xlsx")
