"""Generic (auto-built) specs: a new ticker must value itself from fetched data.

The fixture is a real Yahoo snapshot for 0883.HK (statements in CNY, price in
HKD), so the currency path is covered too. No network: FX and the AI step are
patched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.pipeline import generic_spec as G
from src.pipeline.model_builder import build_and_run

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "yahoo_fundamentals_0883HK.json"


def _fund() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _snap() -> dict:
    return {
        "ticker": "0883.HK",
        "company_name": "CNOOC Limited",
        "price": 22.72,
        "price_date": "2026-11-11",
        "price_url": "https://finance.yahoo.com/quote/0883.HK",
        "valuation_date": "2026-11-11",
        "rf": {"value": 0.03684, "basis": "測試用", "source": "ADB AsianBondsOnline",
               "url": "https://asianbondsonline.adb.org/hongkong/", "data_date": "2026-11-11"},
        "ib_compare": {},
        "data_gaps": [],
        "assumption_source": "rules_fallback",
    }


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Cross-currency FX would hit Yahoo; pin it instead."""
    monkeypatch.setattr(G, "_fx_rate", lambda model, market: 1.15 if model != market else 1.0)
    # keep the Damodaran table deterministic even if the xls is absent
    monkeypatch.setattr(G, "damodaran_betas", lambda: {"Oil/Gas (Production and Exploration)": (0.9855, 0.7464)})


def test_builds_spec_from_statements_only():
    spec = G.build_generic_spec(_snap(), _fund(), {}, use_llm=False)

    assert spec["generic_spec"] is True
    assert spec["ticker"] == "0883.HK"
    # statements are in CNY, the share price is in HKD
    assert spec["currency"] == "CNY"
    assert spec["fx_rate"] == pytest.approx(1.15)
    assert spec["fx_to_model_for_market_cap"] == pytest.approx(1 / 1.15)
    # every path has the forecast length and stays inside sane bounds
    n = len(spec["year_labels"])
    assert n == 10
    for key in ("growths", "margins", "da_pcts", "capex_pcts"):
        assert len(spec[key]) == n
    assert all(-0.6 < g < 3.0 for g in spec["growths"])
    assert all(0.0 <= m < 0.95 for m in spec["margins"])
    assert spec["terminal_g"] <= _snap()["rf"]["value"]
    assert spec["capex_floor_pct"] and spec["shares_floor_m"] > 0
    # provenance: numbers come from Yahoo rows, and the generic caveat is always listed
    assert spec["base_year"]["revenue"] == pytest.approx(398220.0)
    assert spec["base_year"]["ebit"] == pytest.approx(176147.0)
    assert any("通用規格" in g for g in spec["data_gaps"])


def test_generic_spec_runs_through_engine_and_verifies(tmp_path):
    spec = G.build_generic_spec(_snap(), _fund(), {}, use_llm=False)
    result = build_and_run(spec, out_dir=str(tmp_path))

    assert result["verify_pass"] is True, result["verify_errors"]
    out = result["json"]
    assert out["fair_value_per_share"] > 0
    assert out["currency"] == "CNY"
    assert out["fx_if_any"]["per_share_fx"] == pytest.approx(1.15)
    # five scenarios, all numbers real
    assert [s["name"] for s in out["scenarios"]] == ["悲觀", "基準", "樂觀", "靜態", "9%靜態"]
    assert all(s["fair_value"] and s["fair_value"] > 0 for s in out["scenarios"])
    assert out["data_gaps"]


def test_raises_when_statements_are_too_thin():
    thin = {"info": {"industry": "Oil & Gas E&P"}, "is_a": {}, "bs_a": {}, "cf_a": {}}
    with pytest.raises(G.GenericSpecError):
        G.build_generic_spec(_snap(), thin, {}, use_llm=False)


def test_refuses_loss_making_company():
    """A loss-maker gets a nonsense (even negative) fair value, so refuse instead."""
    fund = _fund()
    for date, row in fund["is_a"].items():
        if row.get("EBIT") is not None:
            row["EBIT"] = -abs(row["EBIT"])
            row["Operating Income"] = -abs(row.get("Operating Income") or 1.0)
    with pytest.raises(G.GenericSpecError) as exc:
        G.build_generic_spec(_snap(), fund, {}, use_llm=False)
    assert "EBIT 為負" in str(exc.value)


def test_absurd_consensus_growth_is_clamped_and_reported():
    fund = _fund()
    fund["rev_est"] = {"0y": {"avg": 200000.0, "yearAgoRevenue": 100000.0},
                       "+1y": {"avg": 900000.0, "yearAgoRevenue": None}}
    spec = G.build_generic_spec(_snap(), fund, {}, use_llm=False)
    # 100% -> allowed (<=150%); 350% -> clamped to 150%
    assert spec["growths"][0] == pytest.approx(1.0)
    assert spec["growths"][1] == pytest.approx(1.5)
    assert any("截斷" in g for g in spec["data_gaps"])


def test_validate_paths_accepts_the_rules_path():
    g = [0.05] * 10
    m = [0.2] * 10
    res = {"revenue_growth": g, "ebit_margins": m}
    ok, why = G._validate_paths(res, g, m, 0.025, 10)
    assert ok, why


@pytest.mark.parametrize("mutate,expect", [
    (lambda r: r.update(revenue_growth=[0.05] * 9), "長度"),
    (lambda r: r.update(ebit_margins=[0.2] * 9), "長度"),
    (lambda r: r.update(revenue_growth=[0.9] * 10), "首年增長"),
    (lambda r: r.update(ebit_margins=[0.9] * 10), "首年利潤率"),
    (lambda r: r.update(terminal_g=0.05), "終值"),
])
def test_validate_paths_rejects_drift(mutate, expect):
    g = [0.05] * 10
    m = [0.2] * 10
    res = {"revenue_growth": list(g), "ebit_margins": list(m), "terminal_g": 0.025}
    mutate(res)
    ok, why = G._validate_paths(res, g, m, 0.025, 10)
    assert not ok
    assert expect in why


def test_industry_match_prefers_curated_map_then_tokens():
    table = {"Oil/Gas (Production and Exploration)": (1.0, 1.0),
             "Power": (1.0, 1.0), "Railroad": (1.0, 1.0)}
    assert G._match_industry("Oil & Gas E&P", table) == "Oil/Gas (Production and Exploration)"
    assert G._match_industry("power generation", table) is None  # single token, ambiguous
    assert G._match_industry("", table) is None


def test_market_code_from_suffix():
    assert G.market_code("0883.HK") == "HK"
    assert G.market_code("NVDA") == "US"
    assert G.market_code("7203.T") == "JP"
    assert G.market_code("005930.KS") == "KR"
    assert G.market_code("2330.TW") == "TW"
    assert G.market_code("600519.SS") == "CN"
