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


def add_research_sheets(xlsx_path: str | Path, payload: dict) -> list[str]:
    """Append the research sheets. Returns the names actually written."""
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
        hdr = ["報表", "科目", "中文名", "單位", "重要性", "排名"] + all_years + ["模型輸入"]
        rows = []
        for s in review["statements"]:
            for r in s["rows"]:
                rows.append([
                    s["label"], r["item"], r["label"], r["unit"],
                    r.get("importance"), f"{r.get('rank')}/{r.get('rank_of')}",
                    *[r["values"].get(y) for y in all_years],
                    r.get("model_input") or "",
                ])
        _sheet(wb, "財報覆核", hdr, rows,
               widths=[10, 38, 34, 12, 9, 8] + [13] * len(all_years) + [12],
               note="逐項覆核：Yahoo 財報全部科目，按金額大小排序（比率與每股數字另列其後）。"
                    "「模型輸入」標示哪些科目直接進入估值。")
        written.append("財報覆核")

    adjs = payload.get("adjustments") or []
    rows = [[
        a.get("created_at"), a.get("statement"), a.get("item"), a.get("label"),
        a.get("fy"), a.get("original"), a.get("adjusted"), a.get("delta"),
        a.get("reason"), "是" if a.get("affects_model") else "否",
        (a.get("model_input") or ""), a.get("note") or "",
    ] for a in adjs]
    _sheet(wb, "金額調整紀錄", ["時間", "報表", "科目", "中文名", "財報期", "修改前",
                          "修改後", "差異", "原因", "影響估值", "模型輸入", "說明"],
           rows, widths=[19, 8, 32, 30, 12, 16, 16, 16, 40, 9, 12, 46],
           note="每次修改都保留原始值、修改值、原因與時間；沒有原因的修改不會被採用。" if rows
                else "本次沒有金額調整。")
    written.append("金額調整紀錄")

    fc = payload.get("forecast") or {}
    if fc.get("rows"):
        rows = [[
            r.get("year"), {"actual": "實際", "base": "基準年", "forecast": "預測"}.get(r.get("kind")),
            r.get("period_end"), r.get("revenue"), r.get("growth") if r.get("growth") is not None else None,
            r.get("ebit"), r.get("ebit_margin"), r.get("da"), r.get("capex"),
            r.get("nwc"), r.get("fcff"), r.get("pv"), r.get("growth_base"),
        ] for r in fc["rows"]]
        _sheet(wb, "歷史與預測並列",
               ["年／期間", "類型", "財報期末", "營收", "營收增長", "EBIT", "EBIT 利潤率",
                "D&A", "Capex", "ΔNWC", "FCFF", "現值", "增長依據"],
               rows, widths=[16, 9, 12, 14, 11, 14, 11, 12, 12, 12, 14, 14, 46],
               number_formats={5: "0.0%", 7: "0.0%"},
               note=f"單位：{fc.get('units') or '百萬公司貨幣'}。實際＝已公布財報；基準年＝模型的歷史起點；預測＝本次模型輸出。")
        written.append("歷史與預測並列")

    if fc.get("reasons"):
        rows = [[r.get("year"), r.get("growth"), r.get("growth_rationale"),
                 r.get("margin_rationale"), r.get("reinvestment_rationale")]
                for r in fc["reasons"]]
        _sheet(wb, "預測理由", ["年／期間", "營收增長", "增長理由", "利潤率理由", "再投資理由"],
               rows, widths=[16, 11, 58, 48, 48], number_formats={2: "0.0%"},
               note="理由逐年列出；資料依據與推論分開陳述，未取得資料時明示而不是補一個數字。")
        written.append("預測理由")

    usage = payload.get("usage") or {}
    rows = [[
        "合計", usage.get("requests"), usage.get("reported"), usage.get("prompt_tokens"),
        usage.get("completion_tokens"), usage.get("total_tokens"), usage.get("unreported_requests"),
    ]]
    for ev in usage.get("events") or []:
        rows.append([
            ev.get("tag"), 1, 1 if ev.get("reported") else 0, ev.get("prompt_tokens"),
            ev.get("completion_tokens"), ev.get("total_tokens"), 0 if ev.get("reported") else 1,
        ])
    _sheet(wb, "AI 用量", ["項目", "請求次數", "有回報", "輸入 Token", "輸出 Token",
                        "總 Token", "未回報"], rows,
           widths=[24, 11, 9, 13, 13, 13, 10],
           number_formats={2: "0", 3: "0", 4: "#,##0", 5: "#,##0", 6: "#,##0", 7: "0"},
           note=(usage.get("note") or "未使用 AI 時，此表只會顯示 0 次請求。"))
    written.append("AI 用量")

    hist = payload.get("history") or {}
    rows = [[
        p.get("date"), p.get("price_date"), p.get("fair_value"), p.get("price"),
        p.get("gap"), p.get("gap_pct"), p.get("wacc"), p.get("g"),
        "通過" if p.get("verify_pass") else "未通過" if p.get("verify_pass") is False else "—",
        p.get("assumption_source"),
    ] for p in (hist.get("points") or [])]
    _sheet(wb, "估值歷史", ["估值日", "市價日期", "理論價", "當時市價", "差距", "差距%",
                        "WACC", "終值 g", "驗證", "假設來源"], rows,
           widths=[12, 12, 13, 13, 13, 10, 10, 10, 9, 16],
           number_formats={3: "#,##0.00", 4: "#,##0.00", 5: "#,##0.00", 6: "0.0%", 7: "0.00%", 8: "0.00%"},
           note="每次估值當時的理論價與當時市價；圖表與此表同一資料來源。")
    written.append("估值歷史")

    wb.save(path)
    return written
