"""Document parsers: SEC filings (HTML/iXBRL), Markdown, PDF and structured valuation/statement views."""
from __future__ import annotations

import hashlib
import io
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Optional

from bs4 import BeautifulSoup

SEC_ITEM_RE = re.compile(
    r"^(?:PART\s+[IVX]+\s*[-–—:\.]?\s*)?"
    r"(ITEM\s+\d+[A-Z]?\.?\s*[^\n]{0,120})$",
    re.IGNORECASE,
)
MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*#*$")
WS_RE = re.compile(r"[ \t\u00a0]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Section:
    title: str
    content: str
    page: Optional[int] = None
    url: Optional[str] = None


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    sections: list[Section] = field(default_factory=list)
    url: Optional[str] = None
    sha256: str = ""

    @property
    def full_text(self) -> str:
        return "\n\n".join(f"{s.title}\n{s.content}" if s.title else s.content for s in self.sections)


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [WS_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    out = "\n".join(lines)
    return BLANK_LINES_RE.sub("\n\n", out).strip()


def _sha(data: bytes | str) -> str:
    raw = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def parse_plain_text(text: str, *, title: str = "Document", url: Optional[str] = None) -> ParsedDocument:
    cleaned = _clean_text(text)
    sections = [Section(title=title, content=cleaned, url=url)] if cleaned else []
    return ParsedDocument(title=title, sections=sections, url=url, sha256=_sha(text))


def parse_markdown(text: str, *, title: str = "Report", url: Optional[str] = None) -> ParsedDocument:
    sections: list[Section] = []
    cur_title = title
    buf: list[str] = []
    doc_title = title

    def flush():
        body = _clean_text("\n".join(buf))
        if body:
            sections.append(Section(title=cur_title, content=body, url=url))

    for line in text.replace("\r\n", "\n").split("\n"):
        m = MD_HEADING_RE.match(line.strip())
        if m:
            flush()
            buf.clear()
            heading = m.group(2).strip()
            if len(m.group(1)) == 1 and doc_title == title:
                doc_title = heading
            cur_title = heading
        else:
            buf.append(line)
    flush()
    return ParsedDocument(title=doc_title, sections=sections, url=url, sha256=_sha(text))


def parse_html_filing(html: bytes | str, *, title: str = "SEC Filing", form: str = "",
                      url: Optional[str] = None) -> ParsedDocument:
    """Extract readable text from an SEC HTML/iXBRL filing and split by Item / heading sections."""
    raw_bytes = html.encode("utf-8", errors="replace") if isinstance(html, str) else html
    text_in = html if isinstance(html, str) else html.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text_in, "lxml")

    for tag in soup.find_all(["script", "style", "noscript", "head"]):
        tag.decompose()
    # Inline XBRL hidden header blocks contain raw numeric metadata, not prose
    for tag in soup.find_all(re.compile(r"^(?:ix:)?(hidden|header|references|resources)$", re.I)):
        tag.decompose()

    doc_title = title
    raw_text = soup.get_text("\n")
    lines = [WS_RE.sub(" ", ln).strip() for ln in raw_text.replace("\r\n", "\n").split("\n")]

    sections: list[Section] = []
    cur_title = f"{form} Overview".strip() if form else doc_title
    buf: list[str] = []

    def flush():
        body = _clean_text("\n".join(buf))
        if body and len(body) >= 20:
            sections.append(Section(title=cur_title[:160], content=body, url=url))

    for ln in lines:
        if not ln:
            buf.append("")
            continue
        m = SEC_ITEM_RE.match(ln)
        if m and len(ln) <= 130:
            # Skip table-of-contents lines that end with a bare page number
            if re.search(r"\.{3,}\s*\d+$", ln) or re.search(r"\s+\d{1,3}$", ln):
                buf.append(ln)
                continue
            flush()
            buf.clear()
            cur_title = WS_RE.sub(" ", m.group(1)).strip()
            continue
        buf.append(ln)
    flush()

    if not sections:
        cleaned = _clean_text(raw_text)
        if cleaned:
            sections = [Section(title=cur_title, content=cleaned, url=url)]
    return ParsedDocument(title=doc_title, sections=sections, url=url, sha256=_sha(raw_bytes))


def _extract_pdf_streams_fallback(data: bytes) -> list[str]:
    """Minimal pure-Python fallback for text-bearing PDFs when pypdf is not installed."""
    pages: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        chunk = m.group(1)
        try:
            decoded = zlib.decompress(chunk)
        except Exception:
            decoded = chunk
        if b"Tj" not in decoded and b"TJ" not in decoded:
            continue
        parts: list[str] = []
        for s in re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", decoded):
            parts.append(s.decode("latin-1", errors="ignore"))
        for arr in re.findall(rb"\[(.*?)\]\s*TJ", decoded, re.S):
            for s in re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)", arr):
                parts.append(s.decode("latin-1", errors="ignore"))
        txt = _clean_text(" ".join(p.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\") for p in parts))
        if txt:
            pages.append(txt)
    return pages


def parse_pdf(data: bytes, *, title: str = "PDF Document", url: Optional[str] = None) -> ParsedDocument:
    """Extract text page by page from a PDF (uses pypdf when installed, else built-in stream fallback)."""
    pages: list[str] = []
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(io.BytesIO(data))
        for page in reader.pages:
            pages.append(_clean_text(page.extract_text() or ""))
    except Exception:
        pages = _extract_pdf_streams_fallback(data)

    sections: list[Section] = []
    for idx, txt in enumerate(pages, 1):
        if not txt:
            continue
        first_line = txt.split("\n", 1)[0].strip()
        sec_title = f"Page {idx}: {first_line[:80]}" if first_line else f"Page {idx}"
        sections.append(Section(title=sec_title, content=txt, page=idx, url=url))
    return ParsedDocument(title=title, sections=sections, url=url, sha256=_sha(data))


def build_valuation_doc(calc: dict[str, Any], *, report_md: str = "", url: Optional[str] = None) -> ParsedDocument:
    """Turn a completed valuation run into a structured, citable library document."""
    ticker = str(calc.get("ticker") or "")
    vdate = str(calc.get("valuation_date") or "")
    title = f"{ticker} Valuation Report ({vdate})".strip()
    if report_md.strip():
        return parse_markdown(report_md, title=title, url=url)

    lines = [
        f"# {title}",
        "",
        "## Summary",
        f"- Ticker: {ticker}",
        f"- Company: {calc.get('asset') or ticker}",
        f"- Valuation date: {vdate}",
        f"- Currency: {calc.get('currency') or ''}",
        f"- Market price: {calc.get('price')} ({calc.get('price_date') or ''})",
        f"- Fair value per share (base): {calc.get('fair_value_per_share')}",
        f"- WACC: {(calc.get('discount') or {}).get('wacc', {}).get('value')}",
        f"- Terminal growth g: {(calc.get('terminal') or {}).get('g')}",
        "",
        "## Scenarios",
    ]
    for s in calc.get("scenarios") or []:
        lines.append(f"- {s.get('name')}: fair value {s.get('fair_value')}")
    gaps = calc.get("data_gaps") or []
    if gaps:
        lines += ["", "## Data Gaps and Notes"] + [f"- {g}" for g in gaps]
    return parse_markdown("\n".join(lines), title=title, url=url)


def build_statements_summary_doc(std: dict[str, Any], *, ticker: str, url: Optional[str] = None) -> ParsedDocument:
    """Create a concise searchable overview of standardized statements (exact figures still come from the tool)."""
    years = [y["label"] for y in (std.get("years") or [])][:6]
    name = std.get("name") or ticker
    title = f"{ticker} Standardized Financials Summary ({std.get('template') or 'general'})"
    lines = [
        f"# {title}",
        "",
        "## Overview",
        f"Company: {name} (CIK {std.get('cik') or ''}), currency: {std.get('currency') or ''}, "
        f"template: {std.get('template') or ''}, ruleset: {std.get('ruleset_version') or ''}.",
        f"Covered recent periods: {', '.join(years)}.",
        "",
        "## Key Figures",
    ]
    for row in std.get("rows") or []:
        vals = []
        for y, cell in zip(years, (row.get("cells") or [])[:len(years)]):
            if cell and cell.get("value") is not None:
                vals.append(f"{y}={cell['value']}")
        if vals:
            lines.append(f"- {row['code']} ({row['statement']}): " + ", ".join(vals))
    return parse_markdown("\n".join(lines), title=title, url=url)
