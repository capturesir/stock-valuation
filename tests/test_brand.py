"""Brand stamping (requirement 5.2): name and logo in the Excel and report headers, numbers untouched."""
import json
import shutil
from pathlib import Path

from openpyxl import load_workbook
from PIL import Image

from src.export.brand import stamp_report, stamp_workbook
from src.export.report_md import write_report
from src.export.workbook import build_workbook

FIX = Path(__file__).parent / "fixtures"
NVDA = Path(__file__).parents[1] / "out" / "2026-11-11_NVDA" / "2026-11-11_NVDA.json"   # tracked engine output


def _workbook(tmp_path):
    calc = json.loads(NVDA.read_text(encoding="utf-8"))
    path = tmp_path / "NVDA_FCFF.xlsx"
    build_workbook(calc, str(path))
    return calc, path


def _values(path):
    wb = load_workbook(path)
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}


def test_workbook_gets_name_header_and_logo_without_changing_cells(tmp_path):
    calc, path = _workbook(tmp_path)
    before = _values(path)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (400, 100), (31, 95, 191, 255)).save(logo)

    assert stamp_workbook(path, "R&D 投研台", logo)

    wb = load_workbook(path)
    for ws in wb.worksheets:
        assert ws.oddHeader.left.text == "R&&D 投研台"      # & escaped for header codes
        assert ws.oddHeader.right.text == "&A"
        assert ws.oddFooter.center.text == "&P / &N"
    summary = wb["摘要"]
    assert summary["A2"].value == "R&D 投研台"
    assert len(summary._images) == 1
    ext = summary._images[0].anchor.ext                  # displayed size, scaled from 400×100
    assert (round(ext.width / 9525), round(ext.height / 9525)) == (160, 40)
    after = _values(path)
    after["摘要"][1][0] = None                       # the only cell that changed is the brand line
    assert after == before


def test_svg_or_missing_logo_still_stamps_the_name(tmp_path):
    _, path = _workbook(tmp_path)
    assert stamp_workbook(path, "估值工作台", tmp_path / "missing.png")
    wb = load_workbook(path)
    assert wb["摘要"]["A2"].value == "估值工作台" and not wb["摘要"]._images


def test_formula_like_name_stays_text(tmp_path):
    _, path = _workbook(tmp_path)
    stamp_workbook(path, "=HYPERLINK(\"x\")")
    cell = load_workbook(path)["摘要"]["A2"]
    assert cell.data_type == "s" and cell.value == "=HYPERLINK(\"x\")"


def test_not_a_workbook_is_left_alone(tmp_path):
    f = tmp_path / "x_FCFF.xlsx"
    f.write_text("not excel")
    assert stamp_workbook(f, "名稱") is False
    assert f.read_text() == "not excel"


def test_report_gets_the_name_once(tmp_path):
    md = tmp_path / "AAA_valuation.md"
    shutil.copy(FIX / "AAA_valuation.md", md)
    original = md.read_text(encoding="utf-8")
    assert stamp_report(md, "估值  工作台")
    assert stamp_report(md, "估值 工作台") is False      # stamping twice does not stack
    assert md.read_text(encoding="utf-8") == "> 估值 工作台\n\n" + original


def test_engine_output_is_unbranded(tmp_path):
    # v1 CLI output stays neutral: only the v2 archive copy is stamped
    calc, path = _workbook(tmp_path)
    assert load_workbook(path)["摘要"]["A2"].value is None
    md = tmp_path / "r.md"
    write_report(calc, str(md))
    assert md.read_text(encoding="utf-8").startswith("# ")
