"""Excel: live calc sheet (xlsx_calc) plus 情境假設, 增長假設對照, 投行估值比較, 來源."""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter as CL

from .xlsx_calc import add_calc_sheet, FN, SYM, trade_currency

HDR = Font(name=FN, bold=True, size=11, color="FFFFFF")
HDR_F = PatternFill("solid", fgColor="1F3A5F")
TITLE = Font(name=FN, bold=True, size=14, color="1F3A5F")
TXT = Font(name=FN, size=10)
THIN = Border(
    left=Side(style="thin", color="C8D0DA"), right=Side(style="thin", color="C8D0DA"),
    top=Side(style="thin", color="C8D0DA"), bottom=Side(style="thin", color="C8D0DA"),
)
PCT = "0.00%"
NUM2 = "#,##0.00;-#,##0.00"
NUM4 = "0.0000"


def _header(ws, row, cols):
    for i, h in enumerate(cols, 1):
        c = ws.cell(row, i, h)
        c.font, c.fill, c.border, c.alignment = HDR, HDR_F, THIN, Alignment(wrap_text=True, vertical="center")


def build_workbook(d: dict, path: str) -> str:
    wb = Workbook()
    # summary first
    ws = wb.active
    ws.title = "摘要"
    _summary(ws, d)
    # live calc
    add_calc_sheet(wb, d, "計算")
    _scenarios(wb, d)
    _growth(wb, d)
    _ib(wb, d)
    _sources(wb, d)
    _gaps(wb, d)
    wb.save(path)
    return path


def _summary(ws, d):
    cc = d["currency"]
    tc = trade_currency(d)
    sym = SYM.get(tc, tc)
    ws["A1"] = f"{d['asset']}（{d['ticker']}）FCFF 五情境估值摘要"
    ws["A1"].font = TITLE
    ws.merge_cells("A1:D1")
    rows = [
        ("估值日（澳門時間）", d.get("valuation_date")),
        ("市價", d.get("price")),
        ("市價日期", d.get("price_date")),
        ("貨幣", f"{tc}（模型貨幣 {cc}）" if tc != cc else cc),
        ("基準理論價", d.get("fair_value_per_share")),
        ("市價／基準理論價", d.get("gap_vs_price", {}).get("price_over_fair_ratio")),
        ("WACC（方法 A）", d["discount"]["wacc"]["value"]),
        ("終值 g", d["terminal"]["g"]),
        ("假設來源", d.get("assumption_source") or (d.get("llm_status") or {}).get("assumption_source")),
    ]
    r = 3
    for k, v in rows:
        ws.cell(r, 1, k).font = Font(name=FN, bold=True, size=10)
        cell = ws.cell(r, 2, v)
        cell.font = TXT
        if k in ("市價", "基準理論價"):
            cell.number_format = NUM2
        if k in ("市價／基準理論價",):
            cell.number_format = "0.00x"
        if k in ("WACC（方法 A）", "終值 g"):
            cell.number_format = "0.00%"
        r += 1
    r += 1
    _header(ws, r, ["情境", f"每股理論價（{sym}）", "說明"])
    r += 1
    for s in d.get("scenarios") or []:
        ws.cell(r, 1, s.get("name")).font = TXT
        c = ws.cell(r, 2, s.get("fair_value")); c.number_format = NUM2; c.font = TXT
        ws.cell(r, 3, s.get("changes")).font = Font(name=FN, size=9)
        r += 1
    r += 1
    ws.cell(r, 1, "本文件不構成投資建議。理論價由 Python 引擎按已列來源與假設算出，不是語言模型心算。投行目標價只作核對，不用來回校本框架參數。臨時估值預設不寫入關注名單或訊號。").font = Font(name=FN, size=9, italic=True, color="6B7280")
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 2, end_column=4)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 80


def _scenarios(wb, d):
    ws = wb.create_sheet("情境假設")
    ws["A1"] = "五情境假設"
    ws["A1"].font = TITLE
    _header(ws, 3, ["情境", "每股理論價", "終值 g", "折現率", "增長路徑", "說明"])
    r = 4
    wacc = d["discount"]["wacc"]["value"]
    for s in d.get("scenarios") or []:
        ws.cell(r, 1, s.get("name"))
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
    ws.cell(r, 1, "基準逐年 EBIT 利潤率").font = Font(name=FN, bold=True)
    r += 1
    _header(ws, r, ["財年", "營收增長", "EBIT 利潤率", "Capex/營收", "D&A/營收"])
    r += 1
    for p in d.get("projections") or []:
        ws.cell(r, 1, p.get("year"))
        for col, key, fmt in ((2, "revenue_growth", PCT), (3, "ebit_margin", PCT),
                              (4, "capex_pct_revenue", PCT), (5, "da_pct_revenue", PCT)):
            c = ws.cell(r, col, p.get(key)); c.number_format = fmt
        r += 1
    for i, w in enumerate(["A", "B", "C", "D", "E", "F"], 1):
        ws.column_dimensions[w].width = 22 if w != "F" else 70


def _growth(wb, d):
    ws = wb.create_sheet("增長假設對照")
    ws["A1"] = "未來多年增長假設對照（營收同比，除非另註）"
    ws["A1"].font = TITLE
    gt = d.get("growth_table") or {}
    years = gt.get("years") or [p["year"] for p in d.get("projections") or []]
    cols = ["財年", "本框架－悲觀", "本框架－基準", "本框架－樂觀", "本框架－靜態", "本框架－9%靜態",
            "一致預期營收增長", "基準 EBIT 利潤率"]
    _header(ws, 3, cols)
    keys = ["本框架-悲觀", "本框架-基準", "本框架-樂觀", "本框架-靜態", "本框架-9%靜態", "一致預期營收增長"]
    margins = gt.get("基準EBIT利潤率") or [p.get("ebit_margin") for p in d.get("projections") or []]
    for i, y in enumerate(years):
        r = 4 + i
        ws.cell(r, 1, y)
        for j, k in enumerate(keys, 2):
            series = gt.get(k) or []
            v = series[i] if i < len(series) else None
            c = ws.cell(r, j, v if v is not None else "無")
            if isinstance(v, float):
                c.number_format = PCT
        if i < len(margins) and margins[i] is not None:
            ws.cell(r, 8, margins[i]).number_format = PCT
    r = 5 + len(years)
    ws.cell(r, 1, "終值 g").font = Font(name=FN, bold=True)
    tg = gt.get("終值g") or {}
    mapping = [("悲觀", 2), ("基準", 3), ("樂觀", 4), ("靜態", 5), ("9%靜態", 6)]
    for name, col in mapping:
        if name in tg:
            ws.cell(r, col, tg[name]).number_format = PCT
    r += 2
    ws.cell(r, 1, "附註").font = Font(name=FN, bold=True)
    r += 1
    for note in gt.get("notes") or []:
        ws.cell(r, 1, "• " + note)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        r += 1
    r += 1
    ws.cell(r, 1, "來源").font = Font(name=FN, bold=True)
    r += 1
    for s in gt.get("sources") or []:
        ws.cell(r, 1, s.get("name")); ws.cell(r, 2, s.get("url")); ws.cell(r, 3, s.get("data_date"))
        r += 1
    for i in range(1, 9):
        ws.column_dimensions[CL(i)].width = 18


def _ib(wb, d):
    ws = wb.create_sheet("投行估值比較")
    ws["A1"] = "投行／賣方估值比較（只作核對，不回校本框架）"
    ws["A1"].font = TITLE
    ib = d.get("ib_compare") or {}
    cons = ib.get("consensus") or {}
    r = 3
    ws.cell(r, 1, "一致預期目標價（Yahoo 等彙總，不能代替具名機構）").font = Font(name=FN, bold=True)
    r += 1
    _header(ws, r, ["來源", "日期", "均值", "最高", "最低", "中位", "分析師人數", "貨幣", "網址"])
    r += 1
    if cons.get("mean") is None and not cons.get("n_analysts"):
        ws.cell(r, 1, cons.get("source") or "Yahoo Finance Analysis")
        ws.cell(r, 2, cons.get("data_date") or "")
        ws.cell(r, 3, "無／未取得")
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
            ws.cell(r, 1, "均值 ÷ 本框架基準")
            ws.cell(r, 2, cons["mean"] / d["fair_value_per_share"]).number_format = "0.00x"
            ws.cell(r, 3, "均值相對現價")
            ws.cell(r, 4, cons["mean"] / d["price"] - 1).number_format = PCT
    r += 3
    _header(ws, r, ["機構", "報告日期", "評級", "目標價", "相對現價", "目標價÷基準理論價", "已知方法", "已知關鍵假設", "來源", "備註"])
    r += 1
    brokers = ib.get("named_brokers") or []
    if not brokers:
        ws.cell(r, 1, "（無具名機構。未取得報告原文或可靠轉述，不捏造目標價。）")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
        r += 1
    for b in brokers:
        ws.cell(r, 1, b.get("broker"))
        ws.cell(r, 2, b.get("report_date"))
        ws.cell(r, 3, b.get("rating"))
        c = ws.cell(r, 4, b.get("target")); 
        if isinstance(b.get("target"), (int, float)):
            c.number_format = NUM2
            if d.get("price"):
                ws.cell(r, 5, b["target"] / d["price"] - 1).number_format = PCT
            if d.get("fair_value_per_share"):
                ws.cell(r, 6, b["target"] / d["fair_value_per_share"]).number_format = "0.00x"
        ws.cell(r, 7, b.get("method") or "未披露")
        ws.cell(r, 8, b.get("key_assumptions") or "未披露")
        ws.cell(r, 9, b.get("url"))
        ws.cell(r, 10, b.get("notes"))
        r += 1
    r += 2
    ws.cell(r, 1, "樣本不足" if len(brokers) < 5 else "")
    r += 1
    for g in ib.get("data_gaps") or []:
        ws.cell(r, 1, "缺口：" + str(g)); r += 1
    for i in range(1, 11):
        ws.column_dimensions[CL(i)].width = 18


def _sources(wb, d):
    ws = wb.create_sheet("來源")
    ws["A1"] = "來源與假設依據"
    ws["A1"].font = TITLE
    _header(ws, 3, ["項目", "數值", "依據", "來源名稱", "網址", "資料日期"])
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
    ws.cell(r, 1, "股權橋接").font = Font(name=FN, bold=True); r += 1
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


def _gaps(wb, d):
    ws = wb.create_sheet("資料缺口")
    ws["A1"] = "資料缺口（沒有取到就不填假數）"
    ws["A1"].font = TITLE
    r = 3
    for g in d.get("data_gaps") or []:
        ws.cell(r, 1, "• " + (g if isinstance(g, str) else str(g)))
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        r += 1
    ws.column_dimensions["A"].width = 120
