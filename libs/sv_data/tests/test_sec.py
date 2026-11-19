"""SEC source, raw store and filed layer — offline, from fixtures shaped like the real SEC files."""
from __future__ import annotations

import datetime as dt
import gzip
from decimal import Decimal
from pathlib import Path

import pytest

from libs.sv_data.filed import parse_companyfacts
from libs.sv_data.raw import RawDocument, RawStore
from libs.sv_data.sources import SourceError, SourceNotFound
from libs.sv_data.sources import sec

FIX = Path(__file__).parent / "fixtures"
CONTACT = "Example Research admin@example.com"


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


class FakeHttp:
    def __init__(self, routes: dict[str, tuple[int, bytes]]):
        self.routes, self.calls = routes, []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        status, body = self.routes.get(url, (404, b"not found"))
        return status, body, {}


def test_contact_must_name_an_organisation_and_an_email():
    assert sec.valid_contact(CONTACT)
    for bad in (None, "", "admin@example.com", "Example Research", "x" * 200 + " a@b.co"):
        assert not sec.valid_contact(bad), bad
    with pytest.raises(ValueError):
        sec.SecClient("nobody", FakeHttp({}))


def test_cik_is_ten_digits():
    assert sec.cik10(320193) == "0000320193"
    assert sec.cik10("CIK0000320193") == "0000320193"
    with pytest.raises(ValueError):
        sec.cik10("12a")


def test_client_sends_the_contact_and_decodes_gzip():
    url = sec.FACTS_URL.format(cik="0001234567")
    http = FakeHttp({url: (200, gzip.compress(fixture("companyfacts.json")))})
    doc = sec.SecClient(CONTACT, http, min_interval=0).companyfacts(1234567)
    assert http.calls[0][1]["User-Agent"] == CONTACT
    assert doc.content == fixture("companyfacts.json")
    assert (doc.source, doc.kind, doc.key, doc.url) == ("sec", "companyfacts", "0001234567", url)


def test_client_errors_are_typed():
    client = sec.SecClient(CONTACT, FakeHttp({sec.TICKERS_URL: (503, b"busy")}), min_interval=0)
    with pytest.raises(SourceError) as e:
        client.company_tickers()
    assert e.value.status == 503 and not isinstance(e.value, SourceNotFound)
    with pytest.raises(SourceNotFound):
        client.companyfacts(1)
    with pytest.raises(ValueError):
        client.submissions(1, page="../../etc/passwd")


def test_parse_company_tickers_upper_cases_and_keeps_first():
    rows = {r.ticker: r for r in sec.parse_company_tickers(fixture("company_tickers.json"))}
    assert rows["EXMP"].cik == "0001234567" and rows["BRK-B"].cik == "0007654321"
    assert len(rows) == 3


def test_parse_submissions_and_older_pages():
    s = sec.parse_submissions(fixture("submissions.json"))
    assert (s.cik, s.name, s.fiscal_year_end, s.tickers) == ("0001234567", "EXAMPLE CORP", "1231", ["EXMP"])
    assert [f.form for f in s.filings] == ["10-K", "10-Q", "10-K"]
    f = s.filings[0]
    assert f.filed == dt.date(2025, 2, 20) and f.report_date == dt.date(2024, 12, 31)
    assert f.accepted_at == dt.datetime(2025, 2, 20, 16, 5, tzinfo=dt.timezone.utc)
    assert f.is_inline_xbrl and f.primary_document == "exmp-20241231.htm"
    assert s.older_pages == ["CIK0001234567-submissions-001.json"]
    old = sec.parse_submissions_page(fixture("submissions_page.json"))
    assert [(o.accession, o.is_xbrl) for o in old] == [("0001234567-09-000001", False)]


def test_filing_urls():
    assert sec.filing_index_url("0001234567", "0001234567-25-000010") == \
        "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000010/0001234567-25-000010-index.htm"
    assert sec.filing_document_url(1234567, "0001234567-25-000010", "exmp-20241231.htm").endswith(
        "/1234567/000123456725000010/exmp-20241231.htm")


def test_companyfacts_keeps_every_filing_and_exact_decimals():
    p = parse_companyfacts(fixture("companyfacts.json"))
    assert (p.cik, p.entity_name) == ("0001234567", "EXAMPLE CORP")
    rev = sorted((f for f in p.facts if f.concept == "Revenues"), key=lambda f: (f.period_end, f.filed))
    fy2023 = [f for f in rev if f.period_end == dt.date(2023, 12, 31)]
    # the restatement in the next 10-K is a second fact, not an overwrite
    assert [(f.value, f.accession) for f in fy2023] == [
        (Decimal("60922000000"), "0001234567-24-000005"), (Decimal("60900000000"), "0001234567-25-000010")]
    assert fy2023[0].days == 365 and not fy2023[0].is_instant
    eps = next(f for f in p.facts if f.concept == "EarningsPerShareDiluted" and f.fp == "FY")
    assert eps.value == Decimal("2.94") and eps.unit == "USD/shares"
    assets = [f for f in p.facts if f.concept == "Assets"]
    assert all(f.is_instant for f in assets) and assets[0].frame == "CY2023Q4I"


def test_companyfacts_duplicates_conflicts_and_unreadable_entries_are_counted():
    p = parse_companyfacts(fixture("companyfacts.json"))
    assert p.duplicates == 1          # the Q3 revenue listed twice; the copy with SEC's frame is kept
    q3 = [f for f in p.facts if f.concept == "Revenues" and f.fp == "Q3"]
    assert len(q3) == 1 and q3[0].frame == "CY2024Q3"
    assert p.conflicts == 1           # two different Q3 EPS in one filing: first kept
    assert next(f for f in p.facts if f.concept == "EarningsPerShareDiluted" and f.fp == "Q3").value == Decimal("0.78")
    assert p.skipped == 1             # the 2022 revenue without a filing number
    assert {(c.taxonomy, c.concept) for c in p.concepts} >= {("dei", "EntityCommonStockSharesOutstanding"),
                                                              ("us-gaap", "Revenues")}


def test_raw_store_is_content_addressed_and_verified(tmp_path):
    store = RawStore(tmp_path)
    doc = RawDocument("sec", "companyfacts", "0001234567", "https://x", fixture("companyfacts.json"))
    p1 = store.put(doc)
    p2 = store.put(RawDocument("sec", "companyfacts", "0001234567", "https://x", fixture("companyfacts.json")))
    assert p1 == p2 and p1.name == f"{doc.sha256}.gz"
    rel = store.relative(p1)
    assert store.read(rel) == doc.content
    p1.write_bytes(gzip.compress(b"tampered"))
    with pytest.raises(ValueError):
        store.read(rel)


def test_instance_name_and_download():
    import json as _json

    inline = {"directory": {"item": [{"name": "exmp-20241231.htm"}, {"name": "exmp-20241231_htm.xml"}, {"name": "exmp-20241231_cal.xml"},
                                     {"name": "FilingSummary.xml"}, {"name": "exmp-20241231.xsd"}]}}
    old = {"directory": {"item": [{"name": "exmp-20151231.xml"}, {"name": "exmp-20151231_lab.xml"}, {"name": "R1.xml"},
                                  {"name": "FilingSummary.xml"}]}}
    assert sec.instance_name(_json.dumps(inline).encode()) == "exmp-20241231_htm.xml"
    assert sec.instance_name(_json.dumps(old).encode()) == "exmp-20151231.xml"
    assert sec.instance_name(b'{"directory": {"item": [{"name": "a.htm"}]}}') is None
    base = "https://www.sec.gov/Archives/edgar/data/1234567/000123456725000010"
    http = FakeHttp({base + "/index.json": (200, _json.dumps(inline).encode()),
                     base + "/exmp-20241231_htm.xml": (200, (FIX / "instance.xml").read_bytes())})
    c = sec.SecClient(CONTACT, http, min_interval=0)
    idx = c.filing_index(1234567, "0001234567-25-000010")
    doc = c.instance(1234567, "0001234567-25-000010", sec.instance_name(idx.content))
    assert (doc.kind, doc.key, doc.content_type) == ("instance", "0001234567-25-000010", "application/xml")
    with pytest.raises(ValueError):
        c.instance(1234567, "0001234567-25-000010", "../x.xml")
