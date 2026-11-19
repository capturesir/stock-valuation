"""SEC EDGAR: the official, free XBRL data for US filers (roadmap §3.3).

- ``company_tickers.json`` — every ticker with its CIK
- ``submissions/CIK##########.json`` — company details and every filing (older ones on extra pages)
- ``api/xbrl/companyfacts/CIK##########.json`` — every number the company filed in XBRL

SEC fair-access rules: a User-Agent that names the organisation and an e-mail address (set by an
administrator; requests without one are refused here before they are sent), and no more than
10 requests per second (paced here as well as by the caller).
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import re
import threading
import time
import zlib
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ..raw import RawDocument
from .base import HttpGet, SourceError, SourceNotFound

SOURCE = "sec"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}"
MIN_INTERVAL = 0.11            # seconds between requests: under SEC's 10 per second
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def valid_contact(contact: Optional[str]) -> bool:
    """SEC asks for an organisation name and an e-mail, e.g. "Example Research admin@example.com"."""
    c = (contact or "").strip()
    return bool(EMAIL.search(c)) and len(EMAIL.sub("", c).strip()) >= 2 and len(c) <= 200


def cik10(cik: int | str) -> str:
    digits = str(cik).strip().lstrip("CIK").lstrip("0") or "0"
    if not digits.isdigit():
        raise ValueError(f"not a CIK: {cik!r}")
    return digits.zfill(10)


def filing_index_url(cik: int | str, accession: str) -> str:
    return ARCHIVE_URL.format(cik=int(cik10(cik)), accn=accession.replace("-", "")) + f"/{accession}-index.htm"


def filing_document_url(cik: int | str, accession: str, document: str) -> str:
    return ARCHIVE_URL.format(cik=int(cik10(cik)), accn=accession.replace("-", "")) + f"/{document}"


def _decode(body: bytes, headers: Mapping[str, str]) -> bytes:
    enc = {k.lower(): v for k, v in (headers or {}).items()}.get("content-encoding", "").lower()
    if body[:2] == b"\x1f\x8b" or enc == "gzip":
        return gzip.decompress(body)
    if enc == "deflate":
        return zlib.decompress(body)
    return body


class SecClient:
    def __init__(self, contact: str, http: HttpGet, min_interval: float = MIN_INTERVAL) -> None:
        if not valid_contact(contact):
            raise ValueError("SEC requires a User-Agent with an organisation name and an e-mail address")
        self.contact, self.http, self.min_interval = contact.strip(), http, min_interval
        self._lock, self._last = threading.Lock(), 0.0

    def _get(self, kind: str, key: str, url: str, kind_json: bool = True) -> RawDocument:
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        headers = {"User-Agent": self.contact, "Accept": "application/json" if kind_json else "application/xml",
                   "Accept-Encoding": "gzip, deflate"}
        status, body, resp_headers = self.http(url, headers)
        if status == 404:
            raise SourceNotFound(SOURCE, url, status)
        if status != 200:
            detail = (body or b"")[:200].decode("utf-8", errors="replace")
            raise SourceError(SOURCE, url, status, detail)
        content = _decode(body or b"", resp_headers)
        if kind_json:
            try:
                json.loads(content)
            except ValueError as exc:
                raise SourceError(SOURCE, url, status, f"not JSON: {exc}") from exc
        elif not content.lstrip()[:200].lstrip(b"\xef\xbb\xbf").startswith(b"<"):
            raise SourceError(SOURCE, url, status, "not XML")
        return RawDocument(SOURCE, kind, key, url, content, "application/json" if kind_json else "application/xml", status)

    def company_tickers(self) -> RawDocument:
        return self._get("company_tickers", "all", TICKERS_URL)

    def submissions(self, cik: int | str, page: Optional[str] = None) -> RawDocument:
        """The main page, or an older page named in ``filings.files`` (e.g. CIK0000320193-submissions-001.json)."""
        c = cik10(cik)
        name = page or f"CIK{c}.json"
        if not re.fullmatch(r"CIK\d{10}(-submissions-\d{3})?\.json", name):
            raise ValueError(f"unexpected submissions page name: {name!r}")
        return self._get("submissions", c, SUBMISSIONS_URL.format(name=name))

    def companyfacts(self, cik: int | str) -> RawDocument:
        c = cik10(cik)
        return self._get("companyfacts", c, FACTS_URL.format(cik=c))

    def filing_index(self, cik: int | str, accession: str) -> RawDocument:
        """The list of files in one filing (EDGAR's index.json)."""
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise ValueError(f"unexpected accession number: {accession!r}")
        url = ARCHIVE_URL.format(cik=int(cik10(cik)), accn=accession.replace("-", "")) + "/index.json"
        return self._get("filing_index", accession, url)

    def instance(self, cik: int | str, accession: str, name: str) -> RawDocument:
        """One filing's XBRL instance (XML), by the name instance_name() picked from its index."""
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.xml", name):
            raise ValueError(f"unexpected instance file name: {name!r}")
        url = ARCHIVE_URL.format(cik=int(cik10(cik)), accn=accession.replace("-", "")) + f"/{name}"
        return self._get("instance", accession, url, kind_json=False)


# ------------------------------------------------------------------ parsing (pure functions)

@dataclass(frozen=True)
class TickerEntry:
    ticker: str
    cik: str
    title: str


def parse_company_tickers(content: bytes) -> list[TickerEntry]:
    data = json.loads(content)
    rows = data.values() if isinstance(data, dict) else data
    out: dict[str, TickerEntry] = {}
    for r in rows:
        t = str(r.get("ticker") or "").strip().upper()
        if t and r.get("cik_str") is not None:
            out.setdefault(t, TickerEntry(t, cik10(r["cik_str"]), str(r.get("title") or "")))
    return list(out.values())


@dataclass(frozen=True)
class FilingEntry:
    accession: str
    form: str
    filed: Optional[dt.date]
    report_date: Optional[dt.date]
    accepted_at: Optional[dt.datetime]
    primary_document: Optional[str]
    description: Optional[str]
    items: Optional[str]
    is_xbrl: bool
    is_inline_xbrl: bool


def _date(v: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _accepted(v: Any) -> Optional[dt.datetime]:
    if not v:
        return None
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _filings_from_columns(cols: Mapping[str, list]) -> list[FilingEntry]:
    acc = cols.get("accessionNumber") or []

    def col(name: str, i: int) -> Any:
        arr = cols.get(name) or []
        return arr[i] if i < len(arr) else None

    out = []
    for i, a in enumerate(acc):
        if not a:
            continue
        out.append(FilingEntry(
            accession=str(a), form=str(col("form", i) or ""), filed=_date(col("filingDate", i)),
            report_date=_date(col("reportDate", i)), accepted_at=_accepted(col("acceptanceDateTime", i)),
            primary_document=col("primaryDocument", i) or None, description=col("primaryDocDescription", i) or None,
            items=col("items", i) or None, is_xbrl=bool(col("isXBRL", i)), is_inline_xbrl=bool(col("isInlineXBRL", i))))
    return out


@dataclass(frozen=True)
class Submissions:
    cik: str
    name: Optional[str]
    entity_type: Optional[str]
    sic: Optional[str]
    sic_description: Optional[str]
    fiscal_year_end: Optional[str]          # MMDD, e.g. "0128"
    category: Optional[str]
    state_of_incorporation: Optional[str]
    tickers: list[str]
    exchanges: list[str]
    filings: list[FilingEntry]
    older_pages: list[str]                  # names of extra pages with older filings


def parse_submissions(content: bytes) -> Submissions:
    d = json.loads(content)
    filings = d.get("filings") or {}
    return Submissions(
        cik=cik10(d.get("cik") or 0), name=d.get("name"), entity_type=d.get("entityType"), sic=d.get("sic") or None,
        sic_description=d.get("sicDescription") or None, fiscal_year_end=d.get("fiscalYearEnd") or None,
        category=d.get("category") or None, state_of_incorporation=d.get("stateOfIncorporation") or None,
        tickers=[str(t).upper() for t in d.get("tickers") or []], exchanges=[str(e) for e in d.get("exchanges") or [] if e],
        filings=_filings_from_columns(filings.get("recent") or {}),
        older_pages=[f["name"] for f in filings.get("files") or [] if f.get("name")])


def parse_submissions_page(content: bytes) -> list[FilingEntry]:
    """An older page: the same columns as ``filings.recent``, at the top level."""
    return _filings_from_columns(json.loads(content))


LINKBASE_SUFFIXES = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", "_ref.xml")


def instance_name(index_json: bytes) -> Optional[str]:
    """The XBRL instance in a filing's file list: EDGAR's extract of an inline filing ("…_htm.xml"); for older
    filings, the .xml file that is not a linkbase, FilingSummary.xml or a rendered R page (shortest name)."""
    items = (json.loads(index_json).get("directory") or {}).get("item") or []
    names = [str(i.get("name") or "") for i in items]
    htm = sorted(n for n in names if n.endswith("_htm.xml"))
    if htm:
        return htm[0]
    xml = [n for n in names if n.lower().endswith(".xml") and not n.endswith(LINKBASE_SUFFIXES)
           and n.lower() != "filingsummary.xml" and not re.fullmatch(r"R\d+\.xml", n)]
    return sorted(xml, key=lambda n: (len(n), n))[0] if xml else None
