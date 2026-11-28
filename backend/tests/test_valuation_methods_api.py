"""Backend API tests for beta.1 multi-method valuation (D5 bank DDM/RI/PB-ROE, Reverse DCF, Relative, Football Field)."""
from __future__ import annotations

import datetime as dt
import shutil
from decimal import Decimal

from backend.app import ROOT
from backend.app.db import session
from backend.app.models import FiledFactRow, Filer, PriceBar, RawDocumentRow, SecTicker, Security
from backend.app.services.legacy import import_legacy


def _seed_bank_jpm(db) -> None:
    cik = "0000019617"
    now = dt.datetime.now(dt.timezone.utc)
    db.add(Security(ticker="JPM", name="JPMORGAN CHASE & CO", currency="USD"))
    db.add(SecTicker(ticker="JPM", cik=cik, title="JPMORGAN CHASE & CO", updated_at=now))
    db.add(Filer(cik=cik, name="JPMORGAN CHASE & CO", sic="6021", sic_description="National Commercial Banks",
                 tickers=["JPM"], synced_at=now))
    raw = RawDocumentRow(source="sec", kind="companyfacts", key=cik, url="https://data.sec.gov/test",
                         sha256="a" * 64, bytes=10, content_type="application/json", path="raw/a", http_status=200)
    db.add(raw)
    db.flush()

    def add_fact(concept: str, val: str, unit: str, ps: dt.date | None, pe: dt.date, acc: str):
        db.add(FiledFactRow(
            cik=cik, taxonomy="us-gaap", concept=concept, unit=unit, value=Decimal(val),
            period_start=ps, period_end=pe, fy=pe.year, fp="FY", form="10-K",
            accession=acc, filed=dt.date(pe.year + 1, 2, 15), raw_document_id=raw.id,
        ))

    for yr, ni, eq, sh, eps, div, px in (
        (2023, "47800000000", "300000000000", "2900000000", "16.48", "12000000000", 170.0),
        (2024, "54000000000", "325000000000", "2850000000", "18.95", "13000000000", 240.0),
        (2025, "57000000000", "350000000000", "2800000000", "20.36", "14000000000", 265.0),
    ):
        ps, pe, acc = dt.date(yr, 1, 1), dt.date(yr, 12, 31), f"0000019617-{str(yr)[2:]}-000001"
        add_fact("NetIncomeLossAvailableToCommonStockholdersBasic", ni, "USD", ps, pe, acc)
        add_fact("NetIncomeLoss", ni, "USD", ps, pe, acc)
        add_fact("StockholdersEquity", eq, "USD", None, pe, acc)
        add_fact("Assets", "4000000000000", "USD", None, pe, acc)
        add_fact("LiabilitiesAndStockholdersEquity", "4000000000000", "USD", None, pe, acc)
        add_fact("WeightedAverageNumberOfDilutedSharesOutstanding", sh, "shares", ps, pe, acc)
        add_fact("EarningsPerShareDiluted", eps, "USD/shares", ps, pe, acc)
        add_fact("PaymentsOfDividendsCommonStock", div, "USD", ps, pe, acc)
        db.add(PriceBar(ticker="JPM", date=dt.date(yr, 12, 29), open=px, high=px, low=px, close=px, volume=1000))


def test_bank_methods_show_financial_warning_and_ddm_ri_pb_roe(client):
    with session() as db:
        _seed_bank_jpm(db)

    r = client.get("/api/v1/stocks/JPM/methods")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["template"] == "bank" and body["is_financial"] is True
    assert "financial_fcff_warning" in body["warnings"]
    method_names = {m["method"] for m in body["methods"]}
    assert {"ri", "ddm", "pb_roe"} <= method_names
    assert body["composite_value"] is not None and body["composite_value"] > 0
    assert body["weights"]["ri"] > 0 and body["weights"]["ddm"] > 0 and body["weights"]["pb_roe"] > 0
    assert len(body["football_field"]) >= 3


def test_general_stock_methods_include_dcf_reverse_dcf_and_custom_weights(client, tmp_path):
    out = tmp_path / "out"
    shutil.copytree(ROOT / "out" / "2026-11-11_NVDA", out / "2026-11-11_NVDA")
    with session() as db:
        assert import_legacy(db, out_dir=out) == 1

    r = client.get("/api/v1/stocks/NVDA/methods")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_financial"] is False
    assert "financial_fcff_warning" not in body["warnings"]
    assert any(m["method"] == "dcf" for m in body["methods"])
    assert body["composite_value"] is not None and body["composite_value"] > 0
    assert len(body["football_field"]) >= 1
    assert body["reverse_dcf"] is not None
    assert -0.35 <= body["reverse_dcf"]["implied_growth"] <= 0.80


def test_relative_valuation_adjusts_historical_prices_for_stock_splits(client):
    """Unadjusted pre-split PriceBars must be divided by subsequent split factors to match split-adjusted EPS/shares."""
    from backend.app.models import CorporateAction

    cik = "0009999999"
    now = dt.datetime.now(dt.timezone.utc)
    with session() as db:
        db.add(Security(ticker="SPLT", name="Split Corp", currency="USD"))
        db.add(SecTicker(ticker="SPLT", cik=cik, title="Split Corp", updated_at=now))
        db.add(Filer(cik=cik, name="Split Corp", sic="3674", tickers=["SPLT"], synced_at=now))
        raw = RawDocumentRow(source="sec", kind="companyfacts", key=cik, url="https://data.sec.gov/test",
                             sha256="b" * 64, bytes=10, content_type="application/json", path="raw/b", http_status=200)
        db.add(raw)
        db.flush()

        # 10-for-1 split on 2024-06-10: pre-split price $500 -> split-adjusted $50 (20x P/E on $2.50 EPS)
        db.add(CorporateAction(ticker="SPLT", date=dt.date(2024, 6, 10), kind="split", value=10.0))
        for yr, rev, eq, sh, eps, raw_px in (
            (2023, "1000000000", "500000000", "100000000", "2.50", 500.0),   # pre-split: $500 / 10 = $50 -> 20x P/E, 10x P/B
            (2024, "1200000000", "600000000", "100000000", "3.00", 60.0),    # post-split: $60 -> 20x P/E, 10x P/B
            (2025, "1400000000", "700000000", "100000000", "3.50", 70.0),    # post-split: $70 -> 20x P/E, 10x P/B
        ):
            ps, pe, acc = dt.date(yr, 1, 1), dt.date(yr, 12, 31), f"0009999999-{str(yr)[2:]}-000001"
            for concept, val, unit, start in (
                ("Revenues", rev, "USD", ps),
                ("StockholdersEquity", eq, "USD", None),
                ("WeightedAverageNumberOfDilutedSharesOutstanding", sh, "shares", ps),
                ("EarningsPerShareDiluted", eps, "USD/shares", ps),
            ):
                db.add(FiledFactRow(
                    cik=cik, taxonomy="us-gaap", concept=concept, unit=unit, value=Decimal(val),
                    period_start=start, period_end=pe, fy=yr, fp="FY", form="10-K",
                    accession=acc, filed=dt.date(yr + 1, 2, 15), raw_document_id=raw.id,
                ))
            db.add(PriceBar(ticker="SPLT", date=dt.date(yr, 12, 29), open=raw_px, high=raw_px, low=raw_px, close=raw_px, volume=1000))

    r = client.get("/api/v1/stocks/SPLT/methods")
    assert r.status_code == 200, r.text
    rel = next(m for m in r.json()["methods"] if m["method"] == "relative")
    # True multiple is 20x P/E on $3.50 EPS = $70 (NOT $500+ from unadjusted pre-split price!)
    assert 60.0 <= rel["base"] <= 80.0, rel


def test_run_detail_endpoint_returns_glass_box_audit_data(client, tmp_path):
    """GET /api/v1/runs/{run_id}/detail exposes EV bridge, WACC breakdown, terminal value & projections."""
    out = tmp_path / "out"
    shutil.copytree(ROOT / "out" / "2026-11-11_NVDA", out / "2026-11-11_NVDA")
    with session() as db:
        assert import_legacy(db, out_dir=out) == 1

    vals = client.get("/api/v1/stocks/NVDA/valuations")
    assert vals.status_code == 200, vals.text
    run_id = vals.json()["latest"]["id"]

    r = client.get(f"/api/v1/runs/{run_id}/detail")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["available"] is True
    assert d["ticker"] == "NVDA"
    assert d["enterprise_value"] is not None and d["enterprise_value"] > 0
    assert d["equity_value"] is not None and d["equity_value"] > 0
    assert d["sum_pv_fcff"] is not None and d["pv_terminal"] is not None
    assert isinstance(d["bridge"], list) and len(d["bridge"]) >= 1
    assert isinstance(d["projections"], list) and len(d["projections"]) >= 5
    assert isinstance(d["discount"], dict) and "wacc" in d["discount"]
    assert isinstance(d["terminal"], dict) and "g" in d["terminal"]
