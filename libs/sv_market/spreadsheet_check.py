"""Acceptance (roadmap alpha.4): the metrics module must agree with an independent spreadsheet.

``build`` writes a workbook that holds only the two value series and plain spreadsheet formulas
(AVERAGE, STDEV.S, VAR.S, COVARIANCE.S, CORREL, MIN, MAX, SUMSQ …) for every metric. LibreOffice
recalculates it; ``compare`` reads the spreadsheet's answers next to the module's.
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from . import metrics

ROWS = {  # metric → spreadsheet formula over data!B (asset), data!C (benchmark); n = last data row
    "total_return": "=data!B{n}/data!B2-1",
    "annualised_return": "=(data!B{n}/data!B2)^(365.25/(data!A{n}-data!A2))-1",
    "volatility": "=STDEV.S(data!D3:D{n})*SQRT(252)",
    "sharpe": "=AVERAGE(data!G3:G{n})*252/(STDEV.S(data!G3:G{n})*SQRT(252))",
    "sortino": "=AVERAGE(data!G3:G{n})*252/(SQRT(SUMSQ(data!I3:I{n})/COUNT(data!I3:I{n}))*SQRT(252))",
    "max_drawdown": "=MIN(data!L2:L{n})",
    "calmar": "=B3/ABS(B7)",
    "beta": "=COVARIANCE.S(data!D3:D{n},data!E3:E{n})/VAR.S(data!E3:E{n})",
    "correlation": "=CORREL(data!D3:D{n},data!E3:E{n})",
    "alpha": "=(AVERAGE(data!G3:G{n})-B9*AVERAGE(data!H3:H{n}))*252",
    "tracking_error": "=STDEV.S(data!J3:J{n})*SQRT(252)",
    "information_ratio": "=AVERAGE(data!J3:J{n})*252/B12",
}


def build(path: Path, dates: Sequence[dt.date], a: Sequence[float], b: Sequence[float], rf: float) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(["date", "asset", "benchmark", "r_asset", "r_bench", "rf_period", "excess_asset", "excess_bench",
               "downside", "difference", "peak", "drawdown"])
    ws["F2"] = f"=(1+{rf})^(1/252)-1"
    for i, (d, x, y) in enumerate(zip(dates, a, b), start=2):
        ws.cell(i, 1, d)
        ws.cell(i, 2, x)
        ws.cell(i, 3, y)
        ws.cell(i, 11, f"=MAX($B$2:B{i})")
        ws.cell(i, 12, f"=B{i}/K{i}-1")
        if i > 2:
            ws.cell(i, 4, f"=B{i}/B{i-1}-1")
            ws.cell(i, 5, f"=C{i}/C{i-1}-1")
            ws.cell(i, 7, f"=D{i}-$F$2")
            ws.cell(i, 8, f"=E{i}-$F$2")
            ws.cell(i, 9, f"=MIN(G{i},0)")
            ws.cell(i, 10, f"=D{i}-E{i}")
    n = len(dates) + 1
    m = wb.create_sheet("metrics")
    m.append(["metric", "spreadsheet"])
    for k, f in ROWS.items():
        # in the file format, Excel 2010+ functions carry the _xlfn. prefix (shown without it in Excel/LibreOffice)
        f = f.replace("STDEV.S(", "_xlfn.STDEV.S(").replace("VAR.S(", "_xlfn.VAR.S(").replace("COVARIANCE.S(", "_xlfn.COVARIANCE.S(")
        m.append([k, f.format(n=n)])
    wb.save(path)
    return path


def recalculate(path: Path) -> Path:
    """Open and save with LibreOffice so every formula has a computed value."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise FileNotFoundError("LibreOffice (soffice) is not installed")
    out = Path(tempfile.mkdtemp(prefix="sv-calc-"))
    subprocess.run([soffice, "--headless", "--calc", "--convert-to", "xlsx", "--outdir", str(out), str(path)],
                   check=True, capture_output=True, timeout=180)
    return out / path.name


def compare(path: Path, dates, a, b, rf: float) -> list[tuple[str, float, float]]:
    """(metric, spreadsheet value, module value) for every row."""
    from openpyxl import load_workbook

    sheet = {r[0]: r[1] for r in load_workbook(path, data_only=True)["metrics"].iter_rows(min_row=2, values_only=True)}
    mod = {**metrics.summary(dates, a, rf=rf), **metrics.relative(dates, a, b, rf=rf)}
    return [(k, float(sheet[k]), float(mod[k])) for k in ROWS]
