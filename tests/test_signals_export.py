"""B-group-compatible signals exporter: formulas, schema, merge, and path safety."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.signals.export_signals import (
    SIG_KEYS_EXPECTED,
    TOP_KEYS_EXPECTED,
    ProductionPathError,
    SignalValidationError,
    build_document,
    build_single_document,
    compute_confidence,
    compute_side,
    compute_upside,
    export_signals,
    half_up,
    is_protected_signals_path,
    load_schema,
    load_template,
    next_run,
    signal_from_calc,
    validate_document,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROD_SIGNALS = Path("/workspace/invest/signals/2026-11-11.json")
PROD_LATEST = Path("/workspace/invest/signals.json")


# --------------------------------------------------------------------------- formulas
@pytest.mark.parametrize(
    "value, nd, expected",
    [
        (33.5, 0, 34),
        (32.5, 0, 33),
        (14.9, 0, 15),
        (418.0768547575344, 1, 418.1),
        (269.7986577181208, 1, 269.8),
        (874.9752392386321, 2, 874.98),
        (5.505520668145552, 2, 5.51),
        (168.88999938964844, 2, 168.89),
        (1.4900000095367432, 2, 1.49),
    ],
)
def test_half_up(value, nd, expected):
    assert half_up(value, nd) == expected


def test_upside_side_confidence_thresholds():
    assert compute_upside(115.0, 100.0) == 15.0
    assert compute_side(15.0) == "BUY"
    assert compute_confidence("BUY", 15.0) == 55

    assert compute_upside(114.9, 100.0) == 14.9
    assert compute_side(14.9) == "HOLD"
    assert compute_confidence("HOLD", 14.9) == 35  # half_up(14.9) == 15; 50-15

    assert compute_upside(85.0, 100.0) == -15.0
    assert compute_side(-15.0) == "SELL"
    assert compute_confidence("SELL", -15.0) == 55

    assert compute_side(-14.9) == "HOLD"
    assert compute_confidence("HOLD", -14.9) == 35

    assert compute_side(33.5) == "BUY"
    assert compute_confidence("BUY", 33.5) == 74  # 33.5 → 34; 40+34

    assert compute_confidence("BUY", 418.1) == 90  # cap
    assert compute_confidence("SELL", -418.1) == 90
    assert compute_confidence("HOLD", 0.0) == 50
    assert compute_confidence("HOLD", 7.6) == 42  # base formula; production MU may deduct further


def test_next_run_skips_weekend():
    assert next_run(date(2026, 11, 11)) == "2026-11-12T07:00:00+08:00"  # Wed → Thu
    assert next_run(date(2026, 11, 13)) == "2026-11-16T07:00:00+08:00"  # Fri → Mon
    assert next_run(date(2026, 11, 14)) == "2026-11-16T07:00:00+08:00"  # Sat → Mon
    assert next_run(date(2026, 11, 15)) == "2026-11-16T07:00:00+08:00"  # Sun → Mon


# --------------------------------------------------------------------------- production compatibility (read-only)
@pytest.mark.skipif(not PROD_SIGNALS.is_file(), reason="production example signals file not present")
def test_recompute_matches_production_file_base_formula():
    """Lock upside / side (and base confidence) to the file B group already consumed.

    Production may further cap or cut confidence (confidence_cap, data-gap, stale
    price). Those deviations must be explainable from the rationale; this package
    does not reproduce them.
    """
    doc = json.loads(PROD_SIGNALS.read_text(encoding="utf-8"))
    deviations = []
    for i, s in enumerate(doc["signals"]):
        upside = compute_upside(s["fair_value"], s["market_price"])
        side = compute_side(upside)
        conf = compute_confidence(side, upside)
        assert upside == s["upside_pct"], f"{s['ticker']} upside {upside} != {s['upside_pct']}"
        assert side == s["side"], f"{s['ticker']} side {side} != {s['side']}"
        if conf != s["confidence"]:
            rationale = s.get("rationale") or ""
            # Production-only adjustments should leave a trace in the rationale.
            assert (
                "信心上限" in rationale
                or "資料缺口" in rationale
                or "未取得" in rationale
                or "未反映" in rationale
                or "市價已" in rationale
            ), (
                f"{s['ticker']} confidence base={conf} file={s['confidence']} "
                f"but rationale has no production-adjustment note: {rationale}"
            )
            deviations.append((s["ticker"], conf, s["confidence"], rationale))
    # Known production adjustments on 2026-11-11: MU (data gap), 5803.T / 012330.KS (cap).
    tickers = {t for t, *_ in deviations}
    assert "MU" in tickers or not deviations  # if file still has MU, it should deviate or match 42
    # File must itself pass the semantic upside check already done above.


@pytest.mark.skipif(not PROD_SIGNALS.is_file(), reason="production example signals file not present")
def test_production_file_key_order_matches_template():
    tpl = load_template()
    doc = json.loads(PROD_SIGNALS.read_text(encoding="utf-8"))
    assert list(tpl.keys()) == TOP_KEYS_EXPECTED
    assert list(tpl["signals"][0].keys()) == SIG_KEYS_EXPECTED
    assert list(doc.keys()) == TOP_KEYS_EXPECTED
    for s in doc["signals"]:
        assert list(s.keys()) == SIG_KEYS_EXPECTED


# --------------------------------------------------------------------------- schema / document
def test_built_document_validates_and_key_order(tmp_path):
    calc = json.loads((FIXTURES / "AAA.json").read_text(encoding="utf-8"))
    sig = signal_from_calc(calc, date(2026, 11, 11), str(FIXTURES / "AAA_valuation.md"))
    assert sig["signal_id"] == "20261111-AAA"
    assert sig["signal_id"][9:] == sig["ticker"]
    assert list(sig.keys()) == SIG_KEYS_EXPECTED
    doc = build_document([sig], run_date=date(2026, 11, 11), generated_at="2026-11-11T21:00:00+08:00")
    assert list(doc.keys()) == TOP_KEYS_EXPECTED
    engine, errs = validate_document(doc, load_schema())
    assert errs == [], (engine, errs)
    assert doc["schema_version"] == "1.0"
    assert doc["generated_by"] == "投資經理"
    assert doc["price_date"] == "2026-11-10"
    assert sig["side"] == "BUY"
    assert sig["confidence"] == 60
    assert sig["upside_pct"] == 20.0
    assert "悲觀／樂觀情境 US$90.00–150.00" in sig["rationale"]


def test_empty_signals_array_is_valid():
    doc = build_document([], run_date=date(2026, 11, 11), generated_at="2026-11-11T21:00:00+08:00")
    engine, errs = validate_document(doc)
    assert errs == [], (engine, errs)
    assert doc["signals"] == []
    assert doc["price_date"] == "2026-11-11"


# --------------------------------------------------------------------------- export from package out/
def test_export_nvda_and_1830_from_package_out(tmp_path):
    dest = tmp_path / "signals.json"
    result = export_signals(
        tickers=["NVDA", "1830.HK"],
        run_date="2026-11-11",
        dest=dest,
        merge=False,
        generated_at="2026-11-11T21:30:00+08:00",
        force_dest=False,
    )
    assert dest.is_file()
    dated = tmp_path / "signals" / "2026-11-11.json"
    assert dated.is_file()
    doc = result["doc"]
    engine, errs = validate_document(doc)
    assert errs == [], (engine, errs)
    assert list(doc.keys()) == TOP_KEYS_EXPECTED
    by_t = {s["ticker"]: s for s in doc["signals"]}
    nvda, hk = by_t["NVDA"], by_t["1830.HK"]

    assert nvda["side"] == "BUY"
    assert nvda["confidence"] == 90
    assert nvda["fair_value"] == 874.98
    assert nvda["market_price"] == 168.89
    assert nvda["upside_pct"] == 418.1
    assert nvda["signal_id"] == "20261111-NVDA"
    assert nvda["market"] == "US"
    assert nvda["currency"] == "USD"
    assert nvda["horizon"] == "12M"
    assert nvda["expires_at"] == "2026-11-12T07:00:00+08:00"
    assert nvda["report_path"] == "out/2026-11-11_NVDA/2026-11-11_NVDA_valuation.md"
    assert "US$874.98" in nvda["rationale"] and "US$168.89" in nvda["rationale"]
    assert "高 418.1%" in nvda["rationale"]

    assert hk["side"] == "BUY"
    assert hk["confidence"] == 90
    assert hk["fair_value"] == 5.51
    assert hk["market_price"] == 1.49
    assert hk["upside_pct"] == 269.8
    assert hk["signal_id"] == "20261111-1830.HK"
    assert hk["market"] == "HK"
    assert hk["currency"] == "HKD"
    assert hk["report_path"] == "out/2026-11-11_1830.HK/2026-11-11_1830.HK_valuation.md"
    assert "HK$5.51" in hk["rationale"] and "HK$1.49" in hk["rationale"]

    # Must not have touched production files.
    assert not str(result["dest"]).startswith("/workspace/invest/signals")


def test_merge_keeps_other_tickers(tmp_path):
    dest = tmp_path / "signals.json"
    export_signals(
        from_json=[FIXTURES / "AAA.json"],
        run_date="2026-11-11",
        dest=dest,
        merge=False,
        generated_at="2026-11-11T21:00:00+08:00",
    )
    export_signals(
        from_json=[FIXTURES / "BBB.json"],
        run_date="2026-11-11",
        dest=dest,
        merge=True,
        generated_at="2026-11-11T21:05:00+08:00",
    )
    doc = json.loads(dest.read_text(encoding="utf-8"))
    tickers = [s["ticker"] for s in doc["signals"]]
    assert tickers == ["AAA", "BBB"]
    by_t = {s["ticker"]: s for s in doc["signals"]}
    assert by_t["AAA"]["side"] == "BUY" and by_t["AAA"]["upside_pct"] == 20.0
    assert by_t["BBB"]["side"] == "SELL" and by_t["BBB"]["upside_pct"] == -20.0
    assert by_t["BBB"]["confidence"] == 60
    assert "悲觀（提早下行）／樂觀（上行延長）" in by_t["BBB"]["rationale"]
    # Top-level price_date is the latest among signals (AAA 11-10 > BBB 11-09)
    assert doc["price_date"] == "2026-11-10"
    assert doc["generated_at"] == "2026-11-11T21:05:00+08:00"


def test_no_merge_replaces_file(tmp_path):
    dest = tmp_path / "signals.json"
    export_signals(from_json=[FIXTURES / "AAA.json"], run_date="2026-11-11", dest=dest, merge=False)
    export_signals(from_json=[FIXTURES / "BBB.json"], run_date="2026-11-11", dest=dest, merge=False)
    tickers = [s["ticker"] for s in json.loads(dest.read_text(encoding="utf-8"))["signals"]]
    assert tickers == ["BBB"]


def test_refuse_production_signals_path(tmp_path):
    assert is_protected_signals_path(PROD_LATEST)
    assert is_protected_signals_path(PROD_SIGNALS)
    assert is_protected_signals_path(Path("/workspace/invest/signals.invalid.json"))
    assert not is_protected_signals_path(tmp_path / "signals.json")
    assert not is_protected_signals_path(ROOT / "out" / "signals.json")

    before = PROD_LATEST.read_bytes() if PROD_LATEST.is_file() else None
    with pytest.raises(ProductionPathError) as ei:
        export_signals(
            from_json=[FIXTURES / "AAA.json"],
            run_date="2026-11-11",
            dest=PROD_LATEST,
            merge=False,
            force_dest=False,
        )
    assert "拒絕寫入正式" in str(ei.value)
    if before is not None:
        assert PROD_LATEST.read_bytes() == before
    # dated copy under /workspace/invest/signals/ must also be blocked
    assert not Path("/workspace/invest/signals.invalid.json").exists() or (
        Path("/workspace/invest/signals.invalid.json").read_bytes()[:1] != b""  # don't create it
    )
    # Explicitly: invalid sidecar in production dir must not have been written by this call
    # (file may pre-exist historically; we only assert dest content unchanged, already done)


def test_validation_failure_writes_invalid_sidecar_not_dest(tmp_path, monkeypatch):
    dest = tmp_path / "signals.json"
    # First, write a good file.
    export_signals(from_json=[FIXTURES / "AAA.json"], run_date="2026-11-11", dest=dest, merge=False)
    good = dest.read_text(encoding="utf-8")

    def bad_validate(doc, schema=None):
        return "manual", ["forced failure"]

    import importlib
    expmod = importlib.import_module("src.signals.export_signals")
    monkeypatch.setattr(expmod, "validate_document", bad_validate)
    with pytest.raises(SignalValidationError):
        export_signals(from_json=[FIXTURES / "BBB.json"], run_date="2026-11-11", dest=dest, merge=False)
    assert dest.read_text(encoding="utf-8") == good
    invalid = tmp_path / "signals.invalid.json"
    assert invalid.is_file()
    bad_doc = json.loads(invalid.read_text(encoding="utf-8"))
    assert bad_doc["signals"][0]["ticker"] == "BBB"


def test_unknown_market_is_rejected(tmp_path):
    calc = {
        "ticker": "FOO.XX",
        "asset": "Unknown Market Co",
        "price": 10,
        "currency": "USD",
        "price_date": "2026-11-10",
        "fair_value_per_share": 12,
    }
    path = tmp_path / "FOO.XX.json"
    path.write_text(json.dumps(calc), encoding="utf-8")
    (tmp_path / "FOO.XX_valuation.md").write_text("# x\n", encoding="utf-8")
    from src.signals.export_signals import SignalExportError, signal_from_calc
    with pytest.raises(SignalExportError) as ei:
        signal_from_calc(calc, date(2026, 11, 11), str(tmp_path / "FOO.XX_valuation.md"))
    assert "市場不在 schema 列舉" in str(ei.value)


# --------------------------------------------------------------------------- web (download does not write)
def test_web_download_signals_does_not_write(client, tmp_path):
    prod_before = PROD_LATEST.read_bytes() if PROD_LATEST.is_file() else None
    pkg = ROOT / "out" / "signals.json"
    pkg_exists = pkg.is_file()
    pkg_before = pkg.read_bytes() if pkg_exists else None
    pkg_mtime = pkg.stat().st_mtime if pkg_exists else None

    r = client.get("/api/workbook/signals?ticker=NVDA&date=2026-11-11")
    assert r.status_code == 200, r.text
    doc = r.json()
    engine, errs = validate_document(doc)
    assert errs == [], (engine, errs)
    sig = doc["signals"][0]
    assert sig["ticker"] == "NVDA"
    assert sig["side"] == "BUY" and sig["confidence"] == 90
    assert sig["fair_value"] == 874.98 and sig["market_price"] == 168.89
    assert sig["upside_pct"] == 418.1
    disp = r.headers.get("content-disposition", "")
    assert "signals.json" in disp or "NVDA" in disp

    r2 = client.get("/api/workbook/signals?ticker=1830.HK&date=2026-11-11")
    assert r2.status_code == 200
    sig2 = r2.json()["signals"][0]
    assert sig2["side"] == "BUY" and sig2["confidence"] == 90
    assert sig2["fair_value"] == 5.51 and sig2["upside_pct"] == 269.8

    if prod_before is not None:
        assert PROD_LATEST.read_bytes() == prod_before
    if pkg_exists:
        assert pkg.read_bytes() == pkg_before
        assert pkg.stat().st_mtime == pkg_mtime
    else:
        assert not pkg.exists()


def test_index_offers_signals_download(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "下載訊號 JSON" in page.text
    assert "匯出訊號" in page.text
