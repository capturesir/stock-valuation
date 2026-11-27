"""Excel workbook and Markdown valuation report i18n (alpha.3 step 10 part 4)."""
from __future__ import annotations

import json
import string
from pathlib import Path

from openpyxl import load_workbook

from src.export.brand import stamp_report, stamp_workbook
from src.export.i18n import LANGS, catalog, normalize_lang, t
from src.export.report_md import write_report
from src.export.workbook import build_workbook
from src.export.xlsx_extra import add_research_sheets
from src.pipeline.gaps import Gap, encode

ROOT = Path(__file__).resolve().parents[1]
NVDA = ROOT / "out" / "2026-11-11_NVDA" / "2026-11-11_NVDA.json"
HK1830 = ROOT / "out" / "2026-11-11_1830.HK" / "2026-11-11_1830.HK.json"


def _fields(tpl: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(tpl) if f}


def test_every_export_key_has_three_languages_with_identical_placeholders():
    for key, entry in catalog().items():
        assert set(entry) == set(LANGS), key
        placeholders = {lang: _fields(val) for lang, val in entry.items()}
        assert placeholders["zh-Hans"] == placeholders["zh-Hant"], key
        assert placeholders["en"] == placeholders["zh-Hant"], key


def test_normalize_lang():
    assert normalize_lang(None) == "zh-Hant"
    assert normalize_lang("zh-TW") == "zh-Hant"
    assert normalize_lang("zh-HK") == "zh-Hant"
    assert normalize_lang("zh-Hans") == "zh-Hans"
    assert normalize_lang("zh-CN") == "zh-Hans"
    assert normalize_lang("en") == "en"
    assert normalize_lang("en-US") == "en"
    assert normalize_lang("fr") == "zh-Hant"


def test_build_workbook_three_languages_and_formulas(tmp_path):
    calc = json.loads(NVDA.read_text(encoding="utf-8"))
    calc["data_gap_codes"] = encode([
        Gap("consensus_clamped", label="0y", x=1.8, lo=-0.5, hi=1.5),
        "a hand-written note",
    ])

    expected_sheets = {
        "zh-Hant": ["摘要", "計算", "情境假設", "增長假設對照", "投行估值比較", "來源", "資料缺口"],
        "zh-Hans": ["摘要", "计算", "情景假设", "增长假设对照", "投行估值比较", "来源", "数据缺口"],
        "en": ["Summary", "Calculation", "Scenarios", "Growth Assumptions", "Broker Comparison", "Sources", "Data Gaps"],
    }

    for lang, sheets in expected_sheets.items():
        path = tmp_path / f"NVDA_{lang}.xlsx"
        build_workbook(calc, str(path), lang=lang)
        wb = load_workbook(path)
        assert wb.sheetnames == sheets

        # Calc sheet preserves live formulas and localized check string
        calc_ws = wb[sheets[1]]
        formulas = [str(cell.value) for row in calc_ws.iter_rows() for cell in row
                    if isinstance(cell.value, str) and cell.value.startswith("=")]
        assert len(formulas) >= 40
        chk_pass = t("calc.f.check_pass", lang)
        assert any(chk_pass in f for f in formulas), f"missing {chk_pass} formula in {lang}"

        # Data gaps sheet renders encoded gaps in the requested language
        gaps_ws = wb[sheets[6]]
        gap_lines = [gaps_ws.cell(r, 1).value for r in range(3, gaps_ws.max_row + 1) if gaps_ws.cell(r, 1).value]
        assert any("a hand-written note" in line for line in gap_lines)
        if lang == "en":
            assert any("Revenue consensus 0y of 180.0%" in line for line in gap_lines)
        elif lang == "zh-Hans":
            assert any("营收共识0y 180.0%" in line for line in gap_lines)
        else:
            assert any("營收共識0y 180.0%" in line for line in gap_lines)

        # Branding stamps cleanly onto every localized workbook
        assert stamp_workbook(path, "Test Desk")
        wb2 = load_workbook(path)
        assert wb2[sheets[0]]["A2"].value == "Test Desk"


def test_build_workbook_hk_stock_all_languages(tmp_path):
    calc = json.loads(HK1830.read_text(encoding="utf-8"))
    for lang in LANGS:
        path = tmp_path / f"1830HK_{lang}.xlsx"
        build_workbook(calc, str(path), lang=lang)
        wb = load_workbook(path)
        assert len(wb.sheetnames) == 7


def test_write_report_three_languages(tmp_path):
    calc = json.loads(NVDA.read_text(encoding="utf-8"))
    calc["data_gap_codes"] = encode([Gap("consensus_clamped", label="0y", x=1.8, lo=-0.5, hi=1.5)])

    for lang, marker, gap_marker in (
        ("zh-Hant", "## 五情境每股理論價", "營收共識0y 180.0%"),
        ("zh-Hans", "## 五情景每股理论价", "营收共识0y 180.0%"),
        ("en", "## Five-Scenario Fair Value per Share", "Revenue consensus 0y of 180.0%"),
    ):
        md = tmp_path / f"NVDA_{lang}.md"
        write_report(calc, str(md), lang=lang)
        content = md.read_text(encoding="utf-8")
        assert marker in content
        assert gap_marker in content
        assert stamp_report(md, "Test Desk")
        assert md.read_text(encoding="utf-8").startswith("> Test Desk\n\n")


def test_add_research_sheets_three_languages(tmp_path):
    calc = json.loads(NVDA.read_text(encoding="utf-8"))
    payload = {
        "adjustments": [],
        "usage": {"requests": 0, "reported": 0, "prompt_tokens": 0, "completion_tokens": 0,
                  "total_tokens": 0, "unreported_requests": 0, "events": []},
        "history": {"points": [{"date": "2026-11-11", "price_date": "2026-11-10", "fair_value": 150.0,
                                "price": 140.0, "gap": 10.0, "gap_pct": 0.071, "wacc": 0.1, "g": 0.03,
                                "verify_pass": True, "assumption_source": "rules_fallback"}]},
    }
    for lang, expected_hist in (("zh-Hant", "估值歷史"), ("zh-Hans", "估值历史"), ("en", "Valuation History")):
        path = tmp_path / f"extra_{lang}.xlsx"
        build_workbook(calc, str(path), lang=lang)
        written = add_research_sheets(path, payload, lang=lang)
        assert expected_hist in written
        wb = load_workbook(path)
        assert expected_hist in wb.sheetnames
