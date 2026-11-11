"""Existing 2026-11-11 workbooks must render all seven sheets with real content."""
from __future__ import annotations

from pathlib import Path

from src.web.sheets import EXPECTED_SHEETS, render_workbook, sheet_text_blob

ROOT = Path(__file__).resolve().parents[1]


def _xlsx(ticker: str) -> Path:
    return ROOT / "out" / f"2026-11-11_{ticker}" / f"2026-11-11_{ticker}_FCFF.xlsx"


def _assert_all_sheets(ticker: str, must_contain: dict[str, list[str]]):
    data = render_workbook(_xlsx(ticker))
    assert [s["name"] for s in data["sheets"]] == EXPECTED_SHEETS
    assert data["warnings"] == []
    for sheet in data["sheets"]:
        assert sheet["nonempty"] > 0, f"{ticker} 分頁「{sheet['name']}」空白"
        blob = sheet_text_blob(sheet)
        assert blob.strip(), f"{ticker} 分頁「{sheet['name']}」無文字"
        for snippet in must_contain.get(sheet["name"], []):
            assert snippet in blob, f"{ticker} 分頁「{sheet['name']}」缺少「{snippet}」\n---\n{blob[:500]}"
        if sheet["name"] == "計算":
            ev = sheet["eval"]
            assert ev["formulas"] > 0
            assert ev["failed"] == 0
            assert ev["evaluated"] == ev["formulas"]


def test_render_nvda_all_seven_sheets():
    _assert_all_sheets("NVDA", {
        "摘要": ["NVIDIA", "NVDA", "168.89", "874.98", "2026-11-11", "2026-11-10", "USD", "11.99%", "悲觀", "基準", "樂觀", "靜態", "9%靜態"],
        "計算": ["NVIDIA", "11.986%", "FCFF"],
        "情境假設": ["悲觀", "基準", "樂觀", "靜態", "9%靜態", "783.54", "874.98"],
        "增長假設對照": ["本框架－基準", "FY2028"],
        "投行估值比較": ["Yahoo", "325.78", "57"],
        "來源": ["rf", "erp", "wacc"],
        "資料缺口": ["具名投行"],
    })


def test_render_1830_all_seven_sheets():
    _assert_all_sheets("1830.HK", {
        "摘要": ["完美醫療", "1830.HK", "1.49", "5.51", "2026-11-11", "2026-11-10", "HKD", "8.48%", "悲觀", "基準"],
        "計算": ["完美醫療", "8.477%", "FCFF"],
        "情境假設": ["悲觀", "基準", "樂觀", "靜態", "9%靜態", "4.82", "5.51"],
        "增長假設對照": ["本框架－基準"],
        "投行估值比較": ["無／未取得"],
        "來源": ["rf", "wacc"],
        "資料缺口": ["分析師"],
    })
