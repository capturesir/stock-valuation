"""Extra workbook sheets for the v1.1 research features (R23).

The valuation workbook keeps its original seven sheets untouched; these are
appended so a downloaded Excel contains everything the web page shows:
statement review, the adjustment trail, history next to forecast, the reasons,
AI usage, and the accumulated valuation history.

Called from the pipeline after build_workbook(); a failure here is logged and
ignored so it can never cost the user a completed valuation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .i18n import DEFAULT, normalize_lang, t

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
BOLD = Font(bold=True, size=10)
BODY = Font(size=10)
THIN = Side(style="thin", color="D5DCE5")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
NOTE_FONT = Font(size=9, italic=True, color="5C6B7A")


def _sheet(wb, name: str, headers: list[str], rows: Iterable[list[Any]],
           widths: Optional[list[int]] = None, number_formats: Optional[dict[int, str]] = None,
           note: str = ""):
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name)
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(1, c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    for r in rows:
        ws.append(["" if v is None else v for v in r])
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(headers)):
        for cell in row:
            cell.font = BODY
            cell.border = BORDER
            if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                cell.number_format = (number_formats or {}).get(cell.column, "#,##0.00")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, ws.max_row)}"
    for i in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = (
            (widths or [])[i - 1] if widths and i - 1 < len(widths) else 16
        )
    if note:
        ws.append([])
        ws.append([note])
        ws.cell(ws.max_row, 1).font = NOTE_FONT
    return ws


def add_research_sheets(xlsx_path: str | Path, payload: dict, lang: str = DEFAULT) -> list[str]:
    """Append the research sheets. Returns the names actually written."""
    lang = normalize_lang(lang)
    path = Path(xlsx_path)
    if not path.is_file():
        return []
    wb = load_workbook(path)
    written = []

    review = payload.get("review") or {}
    if review.get("statements"):
        # One sheet, but each statement's years become columns. Use the union of
        # years, newest first, so every statement fits in the same grid.
        all_years: list[str] = []
        for s in review["statements"]:
            for y in s["years"]:
                if y not in all_years:
                    all_years.append(y)
        all_years.sort(reverse=True)
        hdr = [
            t("extra.review.hdr.stmt", lang), t("extra.review.hdr.item", lang), t("extra.review.hdr.label", lang),
            t("extra.review.hdr.unit", lang), t("extra.review.hdr.importance", lang), t("extra.review.hdr.rank", lang),
        ] + all_years + [t("extra.review.hdr.model_input", lang)]
        rows = []
        for s in review["statements"]:
            for r in s["rows"]:
                rows.append([
                    s["label"], r["item"], r["label"], r["unit"],
                    r.get("importance"), f"{r.get('rank')}/{r.get('rank_of')}",
                    *[r["values"].get(y) for y in all_years],
                    r.get("model_input") or "",
                ])
        sheet_review = t("sheet.review", lang)
        _sheet(wb, sheet_review, hdr, rows,
               widths=[10, 38, 34, 12, 9, 8] + [13] * len(all_years) + [12],
               note=t("extra.review.note", lang))
        written.append(sheet_review)

    adjs = payload.get("adjustments") or []
    yes_lbl, no_lbl = t("common.yes", lang), t("common.no", lang)
    rows = [[
        a.get("created_at"), a.get("statement"), a.get("item"), a.get("label"),
        a.get("fy"), a.get("original"), a.get("adjusted"), a.get("delta"),
        a.get("reason"), yes_lbl if a.get("affects_model") else no_lbl,
        (a.get("model_input") or ""), a.get("note") or "",
    ] for a in adjs]
    sheet_adj = t("sheet.adjustments", lang)
    _sheet(wb, sheet_adj, [
        t("extra.adj.hdr.time", lang), t("extra.review.hdr.stmt", lang), t("extra.review.hdr.item", lang),
        t("extra.review.hdr.label", lang), t("extra.adj.hdr.fy", lang), t("extra.adj.hdr.orig", lang),
        t("extra.adj.hdr.adj", lang), t("extra.adj.hdr.delta", lang), t("extra.adj.hdr.reason", lang),
        t("extra.adj.hdr.affects", lang), t("extra.review.hdr.model_input", lang), t("extra.adj.hdr.note", lang),
    ], rows, widths=[19, 8, 32, 30, 12, 16, 16, 16, 40, 9, 12, 46],
           note=t("extra.adj.note_some", lang) if rows else t("extra.adj.note_none", lang))
    written.append(sheet_adj)

    fc = payload.get("forecast") or {}
    if fc.get("rows"):
        kind_map = {
            "actual": t("extra.fc.kind.actual", lang),
            "base": t("extra.fc.kind.base", lang),
            "forecast": t("extra.fc.kind.forecast", lang),
        }
        rows = [[
            r.get("year"), kind_map.get(r.get("kind")),
            r.get("period_end"), r.get("revenue"), r.get("growth") if r.get("growth") is not None else None,
            r.get("ebit"), r.get("ebit_margin"), r.get("da"), r.get("capex"),
            r.get("nwc"), r.get("fcff"), r.get("pv"), r.get("growth_base"),
        ] for r in fc["rows"]]
        sheet_fc = t("sheet.forecast", lang)
        _sheet(wb, sheet_fc, [
            t("extra.fc.hdr.period", lang), t("extra.fc.hdr.kind", lang), t("extra.fc.hdr.period_end", lang),
            t("calc.c.rev", lang), t("wb.hdr.rev_growth", lang), "EBIT", t("wb.hdr.ebit_margin", lang),
            "D&A", "Capex", "ΔNWC", "FCFF", t("extra.fc.hdr.pv", lang), t("extra.fc.hdr.growth_base", lang),
        ], rows, widths=[16, 9, 12, 14, 11, 14, 11, 12, 12, 12, 14, 14, 46],
               number_formats={5: "0.0%", 7: "0.0%"},
               note=t("extra.fc.note", lang, units=fc.get("units") or t("extra.fc.default_units", lang)))
        written.append(sheet_fc)

    if fc.get("reasons"):
        rows = [[r.get("year"), r.get("growth"), r.get("growth_rationale"),
                 r.get("margin_rationale"), r.get("reinvestment_rationale")]
                for r in fc["reasons"]]
        sheet_reasons = t("sheet.reasons", lang)
        _sheet(wb, sheet_reasons, [
            t("extra.fc.hdr.period", lang), t("wb.hdr.rev_growth", lang), t("extra.reasons.hdr.growth", lang),
            t("calc.c.r2", lang), t("calc.c.r3", lang),
        ], rows, widths=[16, 11, 58, 48, 48], number_formats={2: "0.0%"},
               note=t("extra.reasons.note", lang))
        written.append(sheet_reasons)

    usage = payload.get("usage") or {}
    rows = [[
        t("common.total", lang), usage.get("requests"), usage.get("reported"), usage.get("prompt_tokens"),
        usage.get("completion_tokens"), usage.get("total_tokens"), usage.get("unreported_requests"),
    ]]
    for ev in usage.get("events") or []:
        rows.append([
            ev.get("tag"), 1, 1 if ev.get("reported") else 0, ev.get("prompt_tokens"),
            ev.get("completion_tokens"), ev.get("total_tokens"), 0 if ev.get("reported") else 1,
        ])
    sheet_usage = t("sheet.usage", lang)
    _sheet(wb, sheet_usage, [
        t("calc.hdr.item", lang), t("extra.usage.hdr.requests", lang), t("extra.usage.hdr.reported", lang),
        t("extra.usage.hdr.prompt", lang), t("extra.usage.hdr.completion", lang),
        t("extra.usage.hdr.total", lang), t("extra.usage.hdr.unreported", lang),
    ], rows, widths=[24, 11, 9, 13, 13, 13, 10],
           number_formats={2: "0", 3: "0", 4: "#,##0", 5: "#,##0", 6: "#,##0", 7: "0"},
           note=(usage.get("note") or t("extra.usage.note_default", lang)))
    written.append(sheet_usage)

    hist = payload.get("history") or {}
    pass_lbl, fail_lbl = t("common.pass", lang), t("common.fail", lang)
    rows = [[
        p.get("date"), p.get("price_date"), p.get("fair_value"), p.get("price"),
        p.get("gap"), p.get("gap_pct"), p.get("wacc"), p.get("g"),
        pass_lbl if p.get("verify_pass") else fail_lbl if p.get("verify_pass") is False else "—",
        p.get("assumption_source"),
    ] for p in (hist.get("points") or [])]
    sheet_hist = t("sheet.history", lang)
    _sheet(wb, sheet_hist, [
        t("extra.hist.hdr.val_date", lang), t("extra.hist.hdr.price_date", lang), t("extra.hist.hdr.fv", lang),
        t("extra.hist.hdr.price", lang), t("extra.hist.hdr.gap", lang), t("extra.hist.hdr.gap_pct", lang),
        "WACC", t("wb.summary.terminal_g", lang), t("extra.hist.hdr.verify", lang),
        t("wb.summary.assumption_source", lang),
    ], rows, widths=[12, 12, 13, 13, 13, 10, 10, 10, 9, 16],
           number_formats={3: "#,##0.00", 4: "#,##0.00", 5: "#,##0.00", 6: "0.0%", 7: "0.00%", 8: "0.00%"},
           note=t("extra.hist.note", lang))
    written.append(sheet_hist)

    wb.save(path)
    return written
