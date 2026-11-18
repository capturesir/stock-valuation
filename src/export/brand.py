"""Put the system's brand (name, optional logo) on a finished Excel workbook and Markdown report.

Requirement 5.2: the name and logo an administrator sets appear in the header of the Excel file
and the report. The engine writes neutral files; the v2 service stamps its own archived copy of
each run with the branding in force when the run finished, like a printed report. Nothing here
touches numbers or formulas.

- Every sheet gets a print header (brand name left, sheet name right) and page numbers in the footer.
- The summary sheet (摘要) gets the brand name in row 2 and the logo top right, if the logo is a PNG
  (Excel cannot embed SVG through openpyxl; an SVG logo is left out and the name still appears).
- The report gets the brand name as its first line.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

LOGO_MAX_H = 40      # px, fits the 36 pt first row
LOGO_MAX_W = 200     # px
SUMMARY = "摘要"


def _hf(text: str) -> str:
    """Escape for Excel header/footer codes, where & starts a code."""
    return text.replace("&", "&&")[:120]


def stamp_workbook(path: str | Path, name: str, logo_png: Optional[str | Path] = None) -> bool:
    """Return True when the file was stamped; False when it is not a readable workbook."""
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    from .xlsx_calc import FN

    path = Path(path)
    try:
        wb = load_workbook(path)
    except Exception:
        return False
    name = (name or "").strip()
    for ws in wb.worksheets:
        for hdr in (ws.oddHeader, ws.evenHeader):
            hdr.left.text, hdr.left.size = _hf(name), 9
            hdr.right.text, hdr.right.size = "&A", 9
        for ftr in (ws.oddFooter, ws.evenFooter):
            ftr.center.text, ftr.center.size = "&P / &N", 9
    ws = wb[SUMMARY] if SUMMARY in wb.sheetnames else wb.worksheets[0]
    if ws["A2"].value in (None, ""):
        cell = ws["A2"]
        cell.value = name
        cell.data_type = "s"          # a name starting with "=" stays text, never a formula
        cell.font = Font(name=FN, size=9, color="6B7280")
    if logo_png and Path(logo_png).is_file():
        _logo(ws, Path(logo_png))
    # print the summary one page wide, so the title, brand and logo stay on the first page
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.page_setup.orientation = "landscape"
    wb.save(path)
    return True


def _logo(ws, png: Path) -> None:
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.utils import get_column_letter
    from openpyxl.utils.units import pixels_to_EMU

    try:
        img = XLImage(str(png))
    except Exception:
        return
    scale = min(LOGO_MAX_H / max(1, img.height), LOGO_MAX_W / max(1, img.width), 1.0)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    img.width, img.height = w, h
    # top right of the widest column used by the summary, clear of the title on the left
    last = max(1, ws.max_column)
    widths = {i: (ws.column_dimensions[get_column_letter(i)].width or 8.43) for i in range(1, last + 1)}
    col = max(widths, key=lambda i: widths[i])
    col_px = int(widths[col] * 7 + 5)
    marker = AnchorMarker(col=col - 1, colOff=pixels_to_EMU(max(0, col_px - w - 8)), row=0, rowOff=pixels_to_EMU(4))
    img.anchor = OneCellAnchor(_from=marker, ext=XDRPositiveSize2D(pixels_to_EMU(w), pixels_to_EMU(h)))
    ws.row_dimensions[1].height = max(ws.row_dimensions[1].height or 0, (h + 8) * 0.75)
    ws.add_image(img)


def stamp_report(path: str | Path, name: str) -> bool:
    path = Path(path)
    if not path.is_file():
        return False
    name = " ".join((name or "").split())
    text = path.read_text(encoding="utf-8")
    line = f"> {name}"
    if not name or text.startswith(line + "\n"):
        return False
    path.write_text(f"{line}\n\n{text}", encoding="utf-8")
    return True
