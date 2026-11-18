"""Acceptance: the metrics module agrees with spreadsheet formulas recalculated by LibreOffice.
Skipped where LibreOffice is not installed (the CI image); run locally, and by scripts/verify_metrics.py."""
from __future__ import annotations

import shutil

import pytest

from libs.sv_market.spreadsheet_check import build, compare, recalculate
from libs.sv_market.tests.test_metrics import D, walk, weekdays


@pytest.mark.skipif(not (shutil.which("soffice") or shutil.which("libreoffice")), reason="LibreOffice not installed")
def test_metrics_match_spreadsheet_formulas(tmp_path):
    dates = weekdays(D(2022, 1, 3), 700)
    a, b = walk(n=700, seed=11), walk(n=700, seed=12, drift=0.0002, vol=0.009)
    out = recalculate(build(tmp_path / "check.xlsx", dates, a, b, rf=0.025))
    for name, sheet, module in compare(out, dates, a, b, rf=0.025):
        assert module == pytest.approx(sheet, rel=1e-9, abs=1e-12), name
