"""Export standardised statements, ratios, growth, lineage and checks as Excel or CSV (roadmap §6).

Labels come from the same dictionaries the web app uses (frontend/packages/i18n/src/locales), so an item
is named once for every place it appears. Values are exported exactly (full units, not millions).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Optional

LOCALES = Path(__file__).resolve().parents[3] / "frontend" / "packages" / "i18n" / "src" / "locales"
LANGS = ("zh-Hant", "zh-Hans", "en")


@lru_cache(maxsize=4)
def labels(lang: str) -> dict:
    lang = lang if lang in LANGS else "zh-Hant"
    try:
        return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
    except OSError:          # an image without the dictionaries still exports, with codes as names
        return {}


def _t(lang: str, key: str, fallback: str) -> str:
    return labels(lang).get(key) or fallback


def _num(v: Optional[str]):
    return None if v is None else Decimal(v)


TEXT = {
    "zh-Hant": {"statements": "標準化財報", "ratios": "比率", "growth": "增長", "lineage": "出處", "checks": "檢查",
                "item": "項目", "period": "期間", "value": "數值", "method": "方法", "rule": "規則", "concept": "申報科目",
                "form": "申報", "accession": "申報編號", "filed": "申報日", "url": "原文", "change": "變動", "split": "分拆調整",
                "severity": "嚴重程度", "check": "檢查", "values": "比較的數字", "difference": "差異", "cagr": "複合增長",
                "company": "公司", "chart": "科目表", "view": "數字版本", "generated": "產生時間", "unit": "單位",
                "view_latest": "最新（含重編）", "view_original": "首次公布", "years": "年"},
    "zh-Hans": {"statements": "标准化财报", "ratios": "比率", "growth": "增长", "lineage": "出处", "checks": "检查",
                "item": "项目", "period": "期间", "value": "数值", "method": "方法", "rule": "规则", "concept": "申报科目",
                "form": "申报", "accession": "申报编号", "filed": "申报日", "url": "原文", "change": "变动", "split": "拆分调整",
                "severity": "严重程度", "check": "检查", "values": "比较的数字", "difference": "差异", "cagr": "复合增长",
                "company": "公司", "chart": "科目表", "view": "数字版本", "generated": "生成时间", "unit": "单位",
                "view_latest": "最新（含重述）", "view_original": "首次公布", "years": "年"},
    "en": {"statements": "Statements", "ratios": "Ratios", "growth": "Growth", "lineage": "Sources", "checks": "Checks",
           "item": "Item", "period": "Period", "value": "Value", "method": "Method", "rule": "Rule", "concept": "Concept",
           "form": "Form", "accession": "Accession", "filed": "Filed", "url": "Document", "change": "Change", "split": "Split factor",
           "severity": "Severity", "check": "Check", "values": "Numbers compared", "difference": "Difference", "cagr": "Compound growth",
           "company": "Company", "chart": "Chart of accounts", "view": "Version", "generated": "Generated", "unit": "Unit",
           "view_latest": "Latest (restated)", "view_original": "As first reported", "years": "years"},
}


def _period_label(lang: str, y: dict) -> str:
    return _t(lang, "std.ttm", "TTM") if y["kind"] == "ttm" else y["label"]


def to_csv(data: dict, lang: str) -> bytes:
    """One table: every statement row and ratio, with exact values; UTF-8 with BOM so Excel reads Chinese."""
    tx = TEXT.get(lang, TEXT["zh-Hant"])
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f"{data['name']} ({data['ticker']}) CIK {data['cik']}", f"{tx['chart']} {data['ruleset']} {data['ruleset_version']}",
                tx["view_" + data["view"]], data["currency"] or ""])
    w.writerow([tx["item"], "code", tx["unit"]] + [f"{_period_label(lang, y)} ({y['end']})" for y in data["years"]])
    for r in data["rows"]:
        unit = {"money": data["currency"] or "", "per_share": f"{data['currency'] or ''}/share", "shares": "shares"}[r["unit"]]
        w.writerow([_t(lang, f"std.item.{r['code']}", r["code"]), r["code"], unit] + [c["value"] if c else "" for c in r["cells"]])
    for r in data["ratios"]["rows"]:
        w.writerow([_t(lang, f"ratio.{r['code']}", r["code"]), r["code"], r["kind"]] + [c["value"] if c else "" for c in r["cells"]])
    for r in data["ratios"]["growth"]:
        w.writerow([f"{_t(lang, f'std.item.{r['code']}', r['code'])} — {tx['growth']}", f"growth.{r['code']}", "pct"] +
                   [c or "" for c in r["cells"]])
    return ("﻿" + out.getvalue()).encode("utf-8")


def to_xlsx(data: dict, lang: str, path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    tx = TEXT.get(lang, TEXT["zh-Hant"])
    bold, head = Font(bold=True), PatternFill("solid", fgColor="EEF2F7")
    money, per_share, pct, times = "#,##0;-#,##0", "#,##0.00##;-#,##0.00##", "0.00%", "0.00"
    wb = Workbook()
    ws = wb.active
    ws.title = tx["statements"][:31]
    ws["A1"] = f"{data['name']} ({data['ticker']})"
    ws["A1"].font = Font(bold=True, size=14)
    # row 2 is left for the system name (branding)
    ws["A3"] = (f"CIK {data['cik']} · {tx['chart']} {data['ruleset']} {data['ruleset_version']} · {tx['view']} "
                f"{tx['view_' + data['view']]} · {data['currency'] or ''} · {tx['generated']} "
                f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='minutes')}")
    years = data["years"]
    hdr = 5
    ws.cell(hdr, 1, tx["item"]).font = bold
    for j, y in enumerate(years, start=2):
        c = ws.cell(hdr, j, f"{_period_label(lang, y)}\n{y['end']}")
        c.font, c.alignment, c.fill = bold, Alignment(wrap_text=True, horizontal="center"), head
    row = hdr + 1
    for st in ("income", "balance", "cash_flow"):
        ws.cell(row, 1, _t(lang, f"std.statement.{st}", st)).font = Font(bold=True, color="1F5FBF")
        row += 1
        for r in [x for x in data["rows"] if x["statement"] == st]:
            ws.cell(row, 1, _t(lang, f"std.item.{r['code']}", r["code"])).font = bold if r.get("total") else Font()
            fmt = money if r["unit"] in ("money", "shares") else per_share
            for j, c in enumerate(r["cells"], start=2):
                if c:
                    cell = ws.cell(row, j, _num(c["value"]))
                    cell.number_format = fmt
                    if r.get("total"):
                        cell.font = bold
            row += 1
        row += 1
    ws.column_dimensions["A"].width = 42
    for j in range(2, len(years) + 2):
        ws.column_dimensions[ws.cell(hdr, j).column_letter].width = 17
    ws.freeze_panes = ws.cell(hdr + 1, 2)

    wr = wb.create_sheet(tx["ratios"][:31])
    wr.cell(1, 1, tx["item"]).font = bold
    for j, y in enumerate(years, start=2):
        wr.cell(1, j, _period_label(lang, y)).font = bold
    row = 2
    for r in data["ratios"]["rows"]:
        wr.cell(row, 1, _t(lang, f"ratio.{r['code']}", r["code"]))
        for j, c in enumerate(r["cells"], start=2):
            if c:
                cell = wr.cell(row, j, _num(c["value"]))
                cell.number_format = pct if r["kind"] == "pct" else ("0.0" if r["kind"] == "days" else times)
        row += 1
    row += 1
    wr.cell(row, 1, tx["growth"]).font = bold
    row += 1
    for r in data["ratios"]["growth"]:
        wr.cell(row, 1, _t(lang, f"std.item.{r['code']}", r["code"]))
        for j, c in enumerate(r["cells"], start=2):
            if c:
                wr.cell(row, j, _num(c)).number_format = pct
        row += 1
    if data["ratios"]["cagr"]:
        row += 1
        wr.cell(row, 1, tx["cagr"]).font = bold
        row += 1
        for g in data["ratios"]["cagr"]:
            wr.cell(row, 1, f"{_t(lang, f'std.item.{g['code']}', g['code'])} · {g['years']} {tx['years']}")
            wr.cell(row, 2, _num(g["value"])).number_format = pct
            row += 1
    wr.column_dimensions["A"].width = 42

    wl = wb.create_sheet(tx["lineage"][:31])
    cols = [tx["item"], tx["period"], tx["value"], tx["method"], tx["rule"], tx["concept"], tx["form"], tx["accession"],
            tx["filed"], tx["change"], tx["split"], tx["url"]]
    for j, h in enumerate(cols, start=1):
        wl.cell(1, j, h).font = bold
    row = 2
    for r in data["rows"]:
        for y, c in zip(years, r["cells"]):
            if not c:
                continue
            vals = [_t(lang, f"std.item.{r['code']}", r["code"]), _period_label(lang, y), _num(c["value"]), c["method"], c["rule_id"],
                    c.get("concept") or (c.get("formula") or ""), c.get("form") or "", c.get("accession") or "", c.get("filed") or "",
                    c.get("change") or "", c.get("split_factor") if c.get("split_factor") not in (None, "1") else "",
                    c.get("document_url") or c.get("index_url") or ""]
            for j, v in enumerate(vals, start=1):
                wl.cell(row, j, v)
            row += 1
    for j, w_ in enumerate((34, 14, 20, 10, 24, 48, 9, 24, 12, 10, 10, 60), start=1):
        wl.column_dimensions[wl.cell(1, j).column_letter].width = w_

    wc = wb.create_sheet(tx["checks"][:31])
    for j, h in enumerate((tx["period"], tx["severity"], tx["check"], tx["values"], tx["difference"]), start=1):
        wc.cell(1, j, h).font = bold
    for i, f in enumerate(data["checks"]["failures"], start=2):
        wc.cell(i, 1, f["period"])
        wc.cell(i, 2, _t(lang, f"check.severity.{f['severity']}", f["severity"]))
        wc.cell(i, 3, _t(lang, f"check.{f['rule_id']}", f["rule_id"]))
        wc.cell(i, 4, ", ".join(f"{k} {v}" for k, v in (f.get("values") or {}).items()))
        if f.get("difference") is not None:
            wc.cell(i, 5, _num(f["difference"])).number_format = pct
    wc.column_dimensions["C"].width = 48
    wc.column_dimensions["D"].width = 70
    wb.save(path)
    return path
