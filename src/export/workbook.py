"""Excel: live calc sheet (xlsx_calc) plus scenarios, growth comparison, broker comparison, sources, data gaps."""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter as CL

from .i18n import DEFAULT, font_name, normalize_lang, render_gaps, scenario_name, t
from .xlsx_calc import add_calc_sheet, SYM, trade_currency

HDR_F = PatternFill("solid", fgColor="1F3A5F")
THIN = Border(
    left=Side(style="thin", color="C8D0DA"), right=Side(style="thin", color="C8D0DA"),
    top=Side(style="thin", color="C8D0DA"), bottom=Side(style="thin", color="C8D0DA"),
)
PCT = "0.00%"
NUM2 = "#,##0.00;-#,##0.00"
NUM4 = "0.0000"


def _fonts(lang: str):
    fn = font_name(lang)
    return (
        Font(name=fn, bold=True, size=11, color="FFFFFF"),  # HDR
        Font(name=fn, bold=True, size=14, color="1F3A5F"),  # TITLE
        Font(name=fn, size=10),                             # TXT
        Font(name=fn, bold=True, size=10),                  # BOLD
        Font(name=fn, size=9),                              # SMALL
        Font(name=fn, size=9, italic=True, color="6B7280"), # DISC
    )


def _header(ws, row, cols, hdr_font):
    for i, h in enumerate(cols, 1):
        c = ws.cell(row, i, h)
        c.font, c.fill, c.border, c.alignment = hdr_font, HDR_F, THIN, Alignment(wrap_text=True, vertical="center")


def build_workbook(d: dict, path: str, lang: str = DEFAULT) -> str:
    lang = normalize_lang(lang)
    wb = Workbook()
    # summary first
    ws = wb.active
    ws.title = t("sheet.summary", lang)
    _summary(ws, d, lang)
    # live calc
    add_calc_sheet(wb, d, t("sheet.calc", lang), lang=lang)
    _scenarios(wb, d, lang)
    _growth(wb, d, lang)
    _ib(wb, d, lang)
    _sources(wb, d, lang)
    _gaps(wb, d, lang)
    wb.save(path)
    return path


def _summary(ws, d, lang: str):
    hdr_f, title_f, txt_f, bold_f, small_f, disc_f = _fonts(lang)
    cc = d["currency"]
    tc = trade_currency(d)
    sym = SYM.get(tc, tc)
    ws["A1"] = t("wb.summary.title", lang, asset=d["asset"], ticker=d["ticker"])
    ws["A1"].font = title_f
    ws.merge_cells("A1:D1")
    rows = [
        (t("wb.summary.val_date", lang), d.get("valuation_date"), None),
        (t("wb.summary.price", lang), d.get("price"), NUM2),
        (t("wb.summary.price_date", lang), d.get("price_date"), None),
        (t("wb.summary.currency", lang), t("wb.summary.currency_pair", lang, tc=tc, cc=cc) if tc != cc else cc, None),
        (t("wb.summary.base_fv", lang), d.get("fair_value_per_share"), NUM2),
        (t("wb.summary.price_over_fv", lang), d.get("gap_vs_price", {}).get("price_over_fair_ratio"), "0.00x"),
        (t("wb.summary.wacc", lang), d["discount"]["wacc"]["value"], "0.00%"),
        (t("wb.summary.terminal_g", lang), d["terminal"]["g"], "0.00%"),
        (t("wb.summary.assumption_source", lang),
         d.get("assumption_source") or (d.get("llm_status") or {}).get("assumption_source"), None),
    ]
    r = 3
    for k, v, fmt in rows:
        ws.cell(r, 1, k).font = bold_f
        cell = ws.cell(r, 2, v)
        cell.font = txt_f
        if fmt:
            cell.number_format = fmt
        r += 1
    r += 1
    _header(ws, r, [t("wb.summary.hdr.scenario", lang), t("wb.summary.hdr.fv", lang, sym=sym),
                    t("wb.summary.hdr.notes", lang)], hdr_f)
    r += 1
    for s in d.get("scenarios") or []:
        ws.cell(r, 1, scenario_name(s.get("name"), lang)).font = txt_f
        c = ws.cell(r, 2, s.get("fair_value")); c.number_format = NUM2; c.font = txt_f
        ws.cell(r, 3, s.get("changes")).font = small_f
        r += 1
    r += 1
    ws.cell(r, 1, t("wb.summary.disclaimer", lang)).font = disc_f
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 2, end_column=4)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 80


def _scenarios(wb, d, lang: str):
    hdr_f, title_f, _, bold_f, _, _ = _fonts(lang)
    ws = wb.create_sheet(t("sheet.scenarios", lang))
    ws["A1"] = t("wb.scenarios.title", lang)
    ws["A1"].font = title_f
    _header(ws, 3, [t("wb.summary.hdr.scenario", lang), t("wb.scenarios.hdr.fv", lang),
                    t("wb.summary.terminal_g", lang), t("wb.scenarios.hdr.disc", lang),
                    t("wb.scenarios.hdr.growths", lang), t("wb.summary.hdr.notes", lang)], hdr_f)
    r = 4
    wacc = d["discount"]["wacc"]["value"]
    for s in d.get("scenarios") or []:
        ws.cell(r, 1, scenario_name(s.get("name"), lang))
        c = ws.cell(r, 2, s.get("fair_value")); c.number_format = NUM2
        g = s.get("terminal_g")
        if g is not None:
            ws.cell(r, 3, g).number_format = PCT
        disc = 0.09 if s.get("name") == "9%靜態" or s.get("wacc") == 0.09 else wacc
        ws.cell(r, 4, disc).number_format = PCT
        gs = s.get("growths")
        ws.cell(r, 5, ", ".join(f"{x:.1%}" for x in gs) if gs else "")
        ws.cell(r, 6, s.get("changes"))
        r += 1
    r += 2
    ws.cell(r, 1, t("wb.scenarios.base_margins", lang)).font = bold_f
    r += 1
    _header(ws, r, [t("wb.hdr.fy", lang), t("wb.hdr.rev_growth", lang), t("wb.hdr.ebit_margin", lang),
                    t("wb.hdr.capex_rev", lang), t("wb.hdr.da_rev", lang)], hdr_f)
    r += 1
    for p in d.get("projections") or []:
        ws.cell(r, 1, p.get("year"))
        for col, key, fmt in ((2, "revenue_growth", PCT), (3, "ebit_margin", PCT),
                              (4, "capex_pct_revenue", PCT), (5, "da_pct_revenue", PCT)):
            c = ws.cell(r, col, p.get(key)); c.number_format = fmt
        r += 1
    for i, w in enumerate(["A", "B", "C", "D", "E", "F"], 1):
        ws.column_dimensions[w].width = 22 if w != "F" else 70


def _growth(wb, d, lang: str):
    hdr_f, title_f, _, bold_f, _, _ = _fonts(lang)
    ws = wb.create_sheet(t("sheet.growth", lang))
    ws["A1"] = t("wb.growth.title", lang)
    ws["A1"].font = title_f
    gt = d.get("growth_table") or {}
    years = gt.get("years") or [p["year"] for p in d.get("projections") or []]
    cols = [
        t("wb.hdr.fy", lang), t("wb.growth.col.bear", lang), t("wb.growth.col.base", lang),
        t("wb.growth.col.bull", lang), t("wb.growth.col.static", lang), t("wb.growth.col.static_9", lang),
        t("wb.growth.col.consensus", lang), t("wb.growth.col.base_margin", lang),
    ]
    _header(ws, 3, cols, hdr_f)
    keys = ["本框架-悲觀", "本框架-基準", "本框架-樂觀", "本框架-靜態", "本框架-9%靜態", "一致預期營收增長"]
    margins = gt.get("基準EBIT利潤率") or [p.get("ebit_margin") for p in d.get("projections") or []]
    none_label = t("common.none", lang)
    for i, y in enumerate(years):
        r = 4 + i
        ws.cell(r, 1, y)
        for j, k in enumerate(keys, 2):
            series = gt.get(k) or []
            v = series[i] if i < len(series) else None
            c = ws.cell(r, j, v if v is not None else none_label)
            if isinstance(v, float):
                c.number_format = PCT
        if i < len(margins) and margins[i] is not None:
            ws.cell(r, 8, margins[i]).number_format = PCT
    r = 5 + len(years)
    ws.cell(r, 1, t("wb.summary.terminal_g", lang)).font = bold_f
    tg = gt.get("終值g") or {}
    mapping = [("悲觀", 2), ("基準", 3), ("樂觀", 4), ("靜態", 5), ("9%靜態", 6)]
    for name, col in mapping:
        if name in tg:
            ws.cell(r, col, tg[name]).number_format = PCT
    r += 2
    ws.cell(r, 1, t("wb.growth.notes", lang)).font = bold_f
    r += 1
    for note in gt.get("notes") or []:
        ws.cell(r, 1, "• " + note)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        r += 1
    r += 1
    ws.cell(r, 1, t("wb.growth.sources", lang)).font = bold_f
    r += 1
    for s in gt.get("sources") or []:
        ws.cell(r, 1, s.get("name")); ws.cell(r, 2, s.get("url")); ws.cell(r, 3, s.get("data_date"))
        r += 1
    for i in range(1, 9):
        ws.column_dimensions[CL(i)].width = 18


def _ib(wb, d, lang: str):
    hdr_f, title_f, _, bold_f, _, _ = _fonts(lang)
    ws = wb.create_sheet(t("sheet.ib", lang))
    ws["A1"] = t("wb.ib.title", lang)
    ws["A1"].font = title_f
    ib = d.get("ib_compare") or {}
    cons = ib.get("consensus") or {}
    r = 3
    ws.cell(r, 1, t("wb.ib.consensus_hdr", lang)).font = bold_f
    r += 1
    _header(ws, r, [
        t("wb.ib.col.source", lang), t("wb.ib.col.date", lang), t("wb.ib.col.mean", lang),
        t("wb.ib.col.high", lang), t("wb.ib.col.low", lang), t("wb.ib.col.median", lang),
        t("wb.ib.col.analysts", lang), t("wb.ib.col.currency", lang), t("wb.ib.col.url", lang),
    ], hdr_f)
    r += 1
    if cons.get("mean") is None and not cons.get("n_analysts"):
        ws.cell(r, 1, cons.get("source") or "Yahoo Finance Analysis")
        ws.cell(r, 2, cons.get("data_date") or "")
        ws.cell(r, 3, t("common.none_unavailable", lang))
        ws.cell(r, 9, cons.get("url") or "")
    else:
        ws.cell(r, 1, cons.get("source") or "Yahoo")
        ws.cell(r, 2, cons.get("data_date"))
        for col, k in enumerate(["mean", "high", "low", "median"], 3):
            c = ws.cell(r, col, cons.get(k)); c.number_format = NUM2
        ws.cell(r, 7, cons.get("n_analysts"))
        ws.cell(r, 8, cons.get("currency") or d.get("currency"))
        ws.cell(r, 9, cons.get("url"))
        # vs our FV and price
        r += 1
        if cons.get("mean") and d.get("fair_value_per_share"):
            ws.cell(r, 1, t("wb.ib.mean_over_base", lang))
            ws.cell(r, 2, cons["mean"] / d["fair_value_per_share"]).number_format = "0.00x"
            ws.cell(r, 3, t("wb.ib.mean_vs_price", lang))
            ws.cell(r, 4, cons["mean"] / d["price"] - 1).number_format = PCT
    r += 3
    _header(ws, r, [
        t("wb.ib.broker.name", lang), t("wb.ib.broker.date", lang), t("wb.ib.broker.rating", lang),
        t("wb.ib.broker.target", lang), t("wb.ib.broker.vs_price", lang), t("wb.ib.broker.over_base", lang),
        t("wb.ib.broker.method", lang), t("wb.ib.broker.assumptions", lang),
        t("wb.ib.col.source", lang), t("wb.ib.broker.notes", lang),
    ], hdr_f)
    r += 1
    brokers = ib.get("named_brokers") or []
    if not brokers:
        ws.cell(r, 1, t("wb.ib.no_brokers", lang))
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
        r += 1
    undisclosed = t("common.undisclosed", lang)
    for b in brokers:
        ws.cell(r, 1, b.get("broker"))
        ws.cell(r, 2, b.get("report_date"))
        ws.cell(r, 3, b.get("rating"))
        c = ws.cell(r, 4, b.get("target"))
        if isinstance(b.get("target"), (int, float)):
            c.number_format = NUM2
            if d.get("price"):
                ws.cell(r, 5, b["target"] / d["price"] - 1).number_format = PCT
            if d.get("fair_value_per_share"):
                ws.cell(r, 6, b["target"] / d["fair_value_per_share"]).number_format = "0.00x"
        ws.cell(r, 7, b.get("method") or undisclosed)
        ws.cell(r, 8, b.get("key_assumptions") or undisclosed)
        ws.cell(r, 9, b.get("url"))
        ws.cell(r, 10, b.get("notes"))
        r += 1
    r += 2
    ws.cell(r, 1, t("wb.ib.small_sample", lang) if len(brokers) < 5 else "")
    r += 1
    gap_prefix = t("wb.ib.gap_prefix", lang)
    for g in ib.get("data_gaps") or []:
        ws.cell(r, 1, gap_prefix + str(g)); r += 1
    for i in range(1, 11):
        ws.column_dimensions[CL(i)].width = 18


def _sources(wb, d, lang: str):
    hdr_f, title_f, _, bold_f, _, _ = _fonts(lang)
    ws = wb.create_sheet(t("sheet.sources", lang))
    ws["A1"] = t("wb.sources.title", lang)
    ws["A1"].font = title_f
    _header(ws, 3, [
        t("wb.sources.col.item", lang), t("wb.sources.col.value", lang), t("wb.sources.col.basis", lang),
        t("wb.sources.col.source_name", lang), t("wb.ib.col.url", lang), t("wb.sources.col.data_date", lang),
    ], hdr_f)
    r = 4
    for k in ["rf", "erp", "crp", "beta_ref", "beta_adopted", "ke", "kd_pre_tax", "tax_rate", "wacc"]:
        item = (d.get("discount") or {}).get(k) or {}
        if not item:
            continue
        ws.cell(r, 1, k)
        v = item.get("value")
        c = ws.cell(r, 2, v)
        if isinstance(v, float) and abs(v) < 10:
            c.number_format = "0.000%"
        ws.cell(r, 3, item.get("basis"))
        ws.cell(r, 4, item.get("source_name"))
        ws.cell(r, 5, item.get("url"))
        ws.cell(r, 6, item.get("data_date"))
        r += 1
    r += 1
    ws.cell(r, 1, t("wb.sources.bridge", lang)).font = bold_f; r += 1
    for b in d.get("bridge") or []:
        ws.cell(r, 1, b.get("item"))
        ws.cell(r, 2, b.get("value")).number_format = "#,##0.00"
        ws.cell(r, 3, b.get("basis"))
        ws.cell(r, 4, b.get("source_name"))
        ws.cell(r, 5, b.get("url"))
        ws.cell(r, 6, b.get("data_date"))
        r += 1
    for i, w in enumerate(["A", "B", "C", "D", "E", "F"], 1):
        ws.column_dimensions[w].width = 22 if w not in ("C", "E") else 60


def _gaps(wb, d, lang: str):
    _, title_f, _, _, _, _ = _fonts(lang)
    ws = wb.create_sheet(t("sheet.gaps", lang))
    ws["A1"] = t("wb.gaps.title", lang)
    ws["A1"].font = title_f
    r = 3
    for gap_text in render_gaps(d, lang):
        ws.cell(r, 1, "• " + gap_text)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        r += 1
    ws.column_dimensions["A"].width = 120
