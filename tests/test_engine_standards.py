"""Valuation-standard fixes from the 2026-11 engine review (docs/引擎審查-2026-11.md). No network."""
from __future__ import annotations

import pytest

from src.engine import method_a as MA
from src.engine.fcff_engine import EngineInputError
from src.fetch import damodaran, rf


def test_risk_free_rate_is_in_the_model_currency(monkeypatch):
    # CNY cash flows: China 10y minus Damodaran's China default spread (Method A local-currency practice)
    monkeypatch.setattr(rf, "_adb", lambda url: {"ok": True, "source": "ADB AsianBondsOnline", "url": url,
                                                 "value": 0.0162, "data_date": None, "basis": "香港"})
    monkeypatch.setattr(rf, "_trading_economics", lambda url: {"ok": True, "source": "Trading Economics", "url": url,
                                                               "value": 0.0161, "data_date": "2026-11-17", "basis": "香港"})
    r = rf.for_currency("CNY")
    assert r["ok"] and r["currency"] == "CNY"
    assert r["value"] == pytest.approx(0.0162 - MA.DEFAULT_SPREAD["CN"])
    assert r["data_date"] == "2026-11-17" and "中國" in r["basis"] and "違約息差" in r["basis"]
    assert "cross_check_warning" not in r


def test_sources_that_disagree_are_flagged(monkeypatch):
    monkeypatch.setattr(rf, "_adb", lambda url: {"ok": True, "source": "ADB", "url": url, "value": 0.020})
    monkeypatch.setattr(rf, "_trading_economics", lambda url: {"ok": True, "source": "TE", "url": url, "value": 0.030})
    assert "cross_check_warning" in rf.for_currency("JPY")


def test_unsupported_currency_is_not_silently_mixed():
    r = rf.for_currency("BRL")
    assert r["ok"] is False and "BRL" in r["error"]


def test_erp_is_taken_for_the_valuation_month():
    assert MA.erp("2026-10-15")["value"] == pytest.approx(0.0420)
    nov = MA.erp("2026-11-11")
    assert nov["value"] == pytest.approx(0.0441) and nov["data_date"] == "2026-11-01"
    later = damodaran.erp_for("2027-01-10")
    assert later["stale"] is True and later["date"] == "2026-11-01"
    assert "尚未取得" in MA.erp("2027-01-10")["basis"]


def test_damodaran_home_page_is_parsed():
    text = ("Implied ERP on November 1, 2026 = 4.41 % (Trailing 12 month, with adjusted payout); 3.79% (cash yield) "
            "(with the US treasury rate of 4.94% used as the riskfree rate")
    assert damodaran.parse_implied_erp(text) == {"date": "2026-11-01", "erp": 0.0441, "rf": 0.0494}
    assert damodaran.parse_implied_erp("no number here") is None


def test_bad_inputs_raise_an_engine_error_not_systemexit():
    with pytest.raises(EngineInputError):
        MA.check_g(0.05, 0.04, "TEST")


def test_rows_take_a_per_year_tax_path():
    from src.pipeline.model_builder import _make_rows

    spec = {"capex_pcts": [0.05] * 3, "da_pcts": [0.04] * 3, "tax_rate": 0.25, "tax_rates": [0.28, 0.265, 0.25],
            "dnwc_pct": 0.1, "year_labels": ["FY1", "FY2", "FY3"], "discount_periods": [0.5, 1.5, 2.5],
            "period_lengths": [1.0] * 3, "prev_revenue_for_first": 100.0, "growths": [0.1] * 3, "margins": [0.2] * 3}
    rows = _make_rows(spec, spec["growths"], spec["margins"])
    assert [r["tax_rate"] for r in rows] == [0.28, 0.265, 0.25]
    spec.pop("tax_rates")
    assert {r["tax_rate"] for r in _make_rows(spec, spec["growths"], spec["margins"])} == {0.25}


def test_second_same_day_run_still_verifies(tmp_path):
    # a built-in spec's calc is named after its fixed review date; the run-date copy from an earlier
    # run used to be picked as "the dated copy" and every second run failed verification
    import json as _json
    import shutil
    from pathlib import Path

    from src.engine.verify_fcff import verify

    src = Path(__file__).resolve().parents[1] / "out" / "2026-11-11_NVDA" / "2026-11-11_NVDA.json"
    folder = tmp_path / "2026-11-18_NVDA"
    folder.mkdir()
    dated = folder / "2026-11-11_NVDA.json"
    shutil.copy(src, dated)
    shutil.copy(src, folder / "NVDA_latest.json")
    stale = _json.loads(src.read_text(encoding="utf-8"))
    stale["price"] = stale["price"] + 1            # left behind by the previous run
    (folder / "2026-11-18_NVDA.json").write_text(_json.dumps(stale), encoding="utf-8")
    _, _, errs = verify(str(dated))
    assert "dated copy differs from _latest" not in errs


def test_hkd_risk_free_rate_is_net_of_the_hong_kong_default_spread(monkeypatch):
    # owner decision S9: HKD like every other non-Aaa local currency
    monkeypatch.setattr(rf, "hk_10y", lambda cfg=None: {"ok": True, "value": 0.03686, "source": "ADB", "url": "u",
                                                        "data_date": "2026-11-17", "basis": "香港 10 年期 3.686%"})
    r = rf.for_currency("HKD")
    assert r["value"] == pytest.approx(0.03686 - MA.DEFAULT_SPREAD["HK"]) and r["gross_yield"] == pytest.approx(0.03686)
    assert "違約息差" in r["basis"]


def test_static_scenario_uses_maintenance_capex(tmp_path, monkeypatch):
    # owner decision S8: zero growth needs no growth investment, so capex = D&A in the static case
    import json as _json
    from pathlib import Path

    from src.pipeline import generic_spec as G
    from src.pipeline.model_builder import build_and_run

    monkeypatch.setattr(G, "_fx_rate", lambda a, b: 1.15 if a != b else 1.0)
    monkeypatch.setattr(G, "damodaran_betas", lambda: {"Oil/Gas (Production and Exploration)": (0.9855, 0.7464)})
    fund = _json.loads((Path(__file__).parent / "fixtures" / "yahoo_fundamentals_0883HK.json").read_text(encoding="utf-8"))
    snap = {"ticker": "0883.HK", "company_name": "CNOOC", "price": 22.72, "price_date": "2026-11-11", "price_url": "",
            "valuation_date": "2026-11-11", "ib_compare": {}, "data_gaps": [], "assumption_source": "rules_fallback",
            "rf": {"value": 0.03684, "basis": "t", "source": "t", "url": "", "data_date": "2026-11-11"}}
    spec = G.build_generic_spec(snap, fund, {}, use_llm=False)
    out = build_and_run(spec, str(tmp_path))["json"]
    static = next(s for s in out["scenarios"] if s["name"] == "靜態")
    assert static["capex_pcts"] == spec["da_pcts"] and "Capex＝D&A" in static["changes"]
