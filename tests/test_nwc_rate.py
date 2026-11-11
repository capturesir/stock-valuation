"""ΔNWC rate must come from the engine, not a silent 5% template default."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.export.workbook import build_workbook
from src.export.xlsx_calc import resolve_nwc_rate
from src.web.sheets import render_workbook

ROOT = Path(__file__).resolve().parents[1]
NVDA_RATE = 0.23181190897387213
HK1830_RATE = -0.16973468045376472


def _json(ticker: str) -> dict:
    return json.loads((ROOT / "out" / f"2026-11-11_{ticker}" / f"{ticker}_latest.json").read_text())


def test_resolve_nvda_from_full_year_rows():
    assert abs(resolve_nwc_rate(_json("NVDA")) - NVDA_RATE) < 1e-12


def test_resolve_1830_including_first_full_year():
    assert abs(resolve_nwc_rate(_json("1830.HK")) - HK1830_RATE) < 1e-12


def test_explicit_base_year_nwc_rate_wins():
    d = {
        "base_year": {"nwc_rate": 0.12, "revenue": 100},
        "projections": [
            {"year": "Y1", "period_length_years": 1.0, "revenue": 110, "delta_nwc": 9.0},
            {"year": "Y2", "period_length_years": 1.0, "revenue": 121, "delta_nwc": 9.9},
        ],
    }
    assert resolve_nwc_rate(d) == 0.12


def test_missing_rate_raises_instead_of_defaulting_to_five_percent():
    with pytest.raises(ValueError, match="不可再用 5% 預設"):
        resolve_nwc_rate({"ticker": "X", "base_year": {}, "projections": []})


def _check_xlsx(ticker: str, expected_rate: float) -> None:
    path = ROOT / "out" / f"2026-11-11_{ticker}" / f"2026-11-11_{ticker}_FCFF.xlsx"
    official = _json(ticker)["fair_value_per_share"]
    data = render_workbook(path)
    calc = next(s for s in data["sheets"] if s["name"] == "計算")
    nwc = formula_fv = check = None
    for row in calc["rows"]:
        a = row[0].get("v") if row and isinstance(row[0], dict) else None
        b = row[1] if len(row) > 1 else None
        bv = b.get("v") if isinstance(b, dict) else None
        if a == "ΔNWC 比率（× 營收增量）":
            nwc = bv
        if a and "每股理論價" in str(a) and "股權價值" in str(a):
            formula_fv = bv
        if a and str(a).startswith("核對結果"):
            check = bv or (b.get("d") if isinstance(b, dict) else None)
    assert nwc is not None and abs(nwc - expected_rate) < 1e-12
    assert abs(nwc - 0.05) > 1e-9
    assert formula_fv is not None and abs(formula_fv / official - 1) < 0.005
    assert "相符" in str(check)


def test_exported_nvda_reconciles_within_tolerance():
    _check_xlsx("NVDA", NVDA_RATE)


def test_exported_1830_reconciles_within_tolerance():
    _check_xlsx("1830.HK", HK1830_RATE)


def test_rebuild_from_json_does_not_use_five_percent(tmp_path):
    d = _json("NVDA")
    out = tmp_path / "nvda.xlsx"
    build_workbook(d, str(out))
    data = render_workbook(out)
    calc = next(s for s in data["sheets"] if s["name"] == "計算")
    for row in calc["rows"]:
        a = row[0].get("v") if row and isinstance(row[0], dict) else None
        if a == "ΔNWC 比率（× 營收增量）":
            assert abs(row[1]["v"] - NVDA_RATE) < 1e-12
            return
    raise AssertionError("找不到 ΔNWC 比率輸入格")
