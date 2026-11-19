"""Export a comparison as Excel (chart data, metrics, period returns) or CSV. Labels from the web app's dictionaries."""
from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from .statements_export import _t

PCT = ("total_return", "annualised_return", "volatility", "max_drawdown", "alpha", "tracking_error", "months_beating",
       "best_month", "worst_month", "up_capture", "down_capture")


def compare_csv(data: dict, lang: str) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    labels = [f"{ln['label']} ({ln['ref']})" for ln in data["lines"]]
    w.writerow([_t(lang, "cmp.col.date", "date")] + labels)
    for i, d in enumerate(data["dates"]):
        w.writerow([d] + [ln["values"][i] for ln in data["lines"]])
    w.writerow([])
    w.writerow([_t(lang, "cmp.metrics", "metrics")] + labels)
    for k in data["metrics"][0]:
        if k == "key":
            continue
        w.writerow([_t(lang, f"metric.{k}", k)] + [m.get(k) if m.get(k) is not None else "" for m in data["metrics"]])
    for k in ("1m", "3m", "6m", "ytd", "1y", "3y", "5y"):
        w.writerow([_t(lang, f"cmp.period.{k}", k)] + [p.get(k) if p.get(k) is not None else "" for p in data["periods"]])
    return ("﻿" + out.getvalue()).encode("utf-8")


def compare_xlsx(data: dict, lang: str, db: Session) -> bytes:
    import datetime as dt

    from openpyxl import Workbook
    from openpyxl.styles import Font

    from . import branding

    bold = Font(bold=True)
    labels = [f"{ln['label']} ({ln['ref']})" for ln in data["lines"]]
    wb = Workbook()
    ws = wb.active
    ws.title = _t(lang, "cmp.sheet.metrics", "Metrics")[:31]
    ws["A1"] = f"{_t(lang, 'cmp.title', 'Comparison')} · {data['window']['start']} – {data['window']['end']} · {data['currency']}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A3"] = (f"{_t(lang, 'cmp.benchmark', 'Benchmark')}: {labels[int(data['benchmark'][1:])]} · rf {data['rf']:.2%} · "
                f"{_t(lang, 'cmp.frequency.' + data['frequency'], data['frequency'])}")
    row = 5
    ws.cell(row, 1, _t(lang, "cmp.metrics", "Metrics")).font = bold
    for j, lb in enumerate(labels, start=2):
        ws.cell(row, j, lb).font = bold
    for k in [k for k in data["metrics"][0] if k != "key"]:
        row += 1
        ws.cell(row, 1, _t(lang, f"metric.{k}", k))
        for j, m in enumerate(data["metrics"], start=2):
            v = m.get(k)
            if v is None:
                continue
            c = ws.cell(row, j, v)
            c.number_format = "0.00%" if k in PCT else "0.00"
    row += 2
    ws.cell(row, 1, _t(lang, "cmp.periods", "Period returns")).font = bold
    for k in ("1m", "3m", "6m", "ytd", "1y", "3y", "5y"):
        row += 1
        ws.cell(row, 1, _t(lang, f"cmp.period.{k}", k))
        for j, p in enumerate(data["periods"], start=2):
            if p.get(k) is not None:
                ws.cell(row, j, p[k]).number_format = "0.00%"
    ws.column_dimensions["A"].width = 34
    s = wb.create_sheet(_t(lang, "cmp.sheet.series", "Series")[:31])
    s.append([_t(lang, "cmp.col.date", "date")] + labels)
    for c in s[1]:
        c.font = bold
    for i, d in enumerate(data["dates"]):
        s.append([dt.date.fromisoformat(d)] + [ln["values"][i] for ln in data["lines"]])
    tmp = Path(tempfile.mkdtemp(prefix="sv-cmp-")) / "compare.xlsx"
    try:
        wb.save(tmp)
        branding.stamp_run_files(db, [str(tmp)])
        return tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)
        tmp.parent.rmdir()
