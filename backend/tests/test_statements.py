"""Statement data layers ① raw and ② filed: SEC sync, accumulation, restatements, reading (offline)."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from libs.sv_data.sources import sec as sec_source

FIX = Path(__file__).resolve().parents[2] / "libs" / "sv_data" / "tests" / "fixtures"
CONTACT = "Example Research admin@example.com"
SOURCES = {"sec_organisation": "Example Research", "sec_email": "admin@example.com"}
CIK = "0001234567"
API = "/api/v1"


class FakeSec:
    def __init__(self):
        self.calls: list[str] = []
        self.routes = {
            sec_source.TICKERS_URL: (200, (FIX / "company_tickers.json").read_bytes()),
            sec_source.SUBMISSIONS_URL.format(name=f"CIK{CIK}.json"): (200, (FIX / "submissions.json").read_bytes()),
            sec_source.SUBMISSIONS_URL.format(name=f"CIK{CIK}-submissions-001.json"):
                (200, (FIX / "submissions_page.json").read_bytes()),
            sec_source.FACTS_URL.format(cik=CIK): (200, gzip.compress((FIX / "companyfacts.json").read_bytes())),
        }

    def __call__(self, url, headers):
        assert headers["User-Agent"] == CONTACT
        self.calls.append(url)
        status, body = self.routes.get(url, (404, b""))
        return status, body, {}

    def facts(self, doc: dict):
        self.routes[sec_source.FACTS_URL.format(cik=CIK)] = (200, json.dumps(doc).encode())


@pytest.fixture()
def sec(client, monkeypatch):
    from backend.app.services import statements

    fake = FakeSec()
    monkeypatch.setattr(statements, "_http", fake)
    r = client.put(f"{API}/admin/data-sources", json=SOURCES)
    assert r.status_code == 200 and r.json() == {**SOURCES, "sec_contact": CONTACT, "sec_ready": True}
    return fake


def _db():
    from backend.app.db import session
    return session()


def test_data_sources_are_admin_only_and_validated(client, member):
    empty = {"sec_organisation": None, "sec_email": None, "sec_contact": None, "sec_ready": False}
    assert member.get(f"{API}/admin/data-sources").status_code == 403
    assert member.put(f"{API}/admin/data-sources", json=SOURCES).status_code == 403
    assert client.get(f"{API}/admin/data-sources").json() == empty
    for body, code in (({"sec_organisation": "", "sec_email": "admin@example.com"}, "sec_organisation_invalid"),
                       ({"sec_organisation": "a@b.com", "sec_email": "admin@example.com"}, "sec_organisation_invalid"),
                       ({"sec_organisation": "Example Research", "sec_email": ""}, "email_invalid"),
                       ({"sec_organisation": "Example Research", "sec_email": "not-an-email"}, "email_invalid")):
        r = client.put(f"{API}/admin/data-sources", json=body)
        assert r.status_code == 400 and r.json()["detail"]["code"] == code, body
    r = client.post(f"{API}/admin/statements/sync/EXMP")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "sec_contact_missing"
    r = client.put(f"{API}/admin/data-sources", json={"sec_organisation": "  Example   Research ", "sec_email": " admin@example.com "})
    assert r.json() == {**SOURCES, "sec_contact": CONTACT, "sec_ready": True}          # spaces tidied
    assert client.put(f"{API}/admin/data-sources", json={"sec_organisation": "", "sec_email": ""}).json() == empty


def test_an_older_single_string_contact_is_read_as_two_fields(client):
    from backend.app.models import Setting

    with _db() as db:
        db.add(Setting(key="data_sources", value={"sec_contact": CONTACT}))
        db.commit()
    assert client.get(f"{API}/admin/data-sources").json() == {**SOURCES, "sec_contact": CONTACT, "sec_ready": True}


def test_sync_stores_raw_documents_filings_and_every_fact(client, sec, tmp_path):
    from backend.app.models import FiledFactRow, Filing, RawDocumentRow

    r = client.post(f"{API}/admin/statements/sync/EXMP")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cik"] == CIK and body["name"] == "EXAMPLE CORP"
    assert body["filings_added"] == 4                       # 3 recent + 1 from the older page
    assert body["facts_added"] == body["facts_total"] == 9
    assert (body["duplicates"], body["conflicts"], body["skipped"]) == (1, 1, 1)
    with _db() as db:
        docs = {d.kind: d for d in db.scalars(select(RawDocumentRow))}
        assert set(docs) == {"company_tickers", "submissions", "companyfacts"}
        raw = gzip.decompress((tmp_path / "raw" / docs["companyfacts"].path).read_bytes())
        assert raw == (FIX / "companyfacts.json").read_bytes()          # kept byte for byte
        assert db.scalar(select(func.count()).select_from(Filing)) == 4
        rev = db.scalars(select(FiledFactRow).where(FiledFactRow.concept == "Revenues")
                         .order_by(FiledFactRow.period_end, FiledFactRow.filed)).all()
        assert [str(f.value) for f in rev if f.period_end.year == 2023] == ["60922000000", "60900000000"]


def test_unchanged_source_adds_nothing_and_old_pages_are_read_once(client, sec):
    client.post(f"{API}/admin/statements/sync/EXMP")
    sec.calls.clear()
    r = client.post(f"{API}/admin/statements/sync/EXMP").json()
    assert r["unchanged"] and r["facts_added"] == 0 and r["filings_added"] == 0 and r["facts_total"] == 9
    assert not any("submissions-001" in u for u in sec.calls)
    assert sec_source.TICKERS_URL not in sec.calls            # the ticker list is fresh for a week
    with _db() as db:
        from backend.app.models import RawDocumentRow
        assert db.scalar(select(func.count()).select_from(RawDocumentRow)) == 4   # + the older page


def test_restated_and_dropped_facts_are_kept(client, sec):
    client.post(f"{API}/admin/statements/sync/EXMP")
    doc = json.loads((FIX / "companyfacts.json").read_text())
    rev = doc["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    # a new 10-K restates FY2024, and SEC's new file no longer lists the FY2023 original
    rev[:] = [e for e in rev if e.get("accn") != "0001234567-24-000005"]
    rev.append({"start": "2024-01-01", "end": "2024-12-31", "val": 130000000000, "accn": "0001234567-26-000003",
                "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-19"})
    sec.facts(doc)
    r = client.post(f"{API}/admin/statements/sync/EXMP").json()
    assert not r["unchanged"] and r["facts_added"] == 1 and r["facts_total"] == 10
    facts = client.get(f"{API}/stocks/EXMP/filed-facts", params={"concept": "Revenues"}).json()
    fy2024 = [f for f in facts if f["period_end"] == "2024-12-31"]
    assert [(f["value"], f["accession"]) for f in fy2024] == [
        ("130000000000", "0001234567-26-000003"), ("130497000000", "0001234567-25-000010")]
    assert any(f["accession"] == "0001234567-24-000005" for f in facts)      # the dropped original is still here


def test_reading_filed_data_as_a_member(client, sec, member):
    assert member.get(f"{API}/stocks/EXMP/filer").json()["detail"]["code"] == "statements_not_synced"
    client.post(f"{API}/admin/statements/sync/EXMP")
    f = member.get(f"{API}/stocks/EXMP/filer").json()
    assert (f["cik"], f["fiscal_year_end"], f["filings"], f["facts"], f["sic"]) == (CIK, "1231", 4, 9, "3674")
    concepts = {(c["taxonomy"], c["concept"]): c for c in member.get(f"{API}/stocks/EXMP/filed-concepts").json()}
    assert concepts[("us-gaap", "Revenues")]["facts"] == 4 and concepts[("us-gaap", "Revenues")]["label"] == "Revenues"
    assert concepts[("us-gaap", "EarningsPerShareDiluted")]["units"] == ["USD/shares"]
    eps = member.get(f"{API}/stocks/EXMP/filed-facts", params={"concept": "EarningsPerShareDiluted"}).json()
    fy = next(e for e in eps if e["fp"] == "FY")
    assert fy["value"] == "2.94" and fy["form"] == "10-K"        # exact decimal, not 2.9399999
    assert fy["document_url"].endswith("/1234567/000123456725000010/exmp-20241231.htm")
    assert fy["index_url"].endswith("0001234567-25-000010-index.htm")
    assets = member.get(f"{API}/stocks/EXMP/filed-facts", params={"concept": "Assets"}).json()
    assert assets[0]["period_start"] is None and assets[0]["period_end"] == "2024-12-31"
    assert member.post(f"{API}/admin/statements/sync/EXMP").status_code == 403


def test_non_sec_tickers_and_upstream_failures(client, sec):
    r = client.post(f"{API}/admin/statements/sync/0700.HK")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "sec_not_filer"
    sec.routes[sec_source.SUBMISSIONS_URL.format(name=f"CIK{CIK}.json")] = (503, b"busy")
    r = client.post(f"{API}/admin/statements/sync/EXMP")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "upstream"
    with _db() as db:
        from backend.app.models import RawDocumentRow
        assert db.scalar(select(func.count()).select_from(RawDocumentRow)) == 1   # the ticker list was kept


def test_company_without_xbrl_data(client, sec):
    del sec.routes[sec_source.FACTS_URL.format(cik=CIK)]
    r = client.post(f"{API}/admin/statements/sync/EXMP").json()
    assert r["has_xbrl"] is False and r["facts_total"] == 0 and r["filings_added"] == 4


def test_large_company_is_inserted_in_safe_batches(client, sec):
    """A large filer has 100k+ facts; one INSERT may carry at most 65,535 parameters."""
    doc = json.loads((FIX / "companyfacts.json").read_text())
    many = [{"start": f"{2000 + i // 365}-01-01", "end": f"{2000 + i // 365}-12-31", "val": i,
             "accn": f"0001234567-{i:02d}-{i:06d}"[:20], "fy": 2000, "fp": "FY", "form": "10-K", "filed": "2025-01-01"}
            for i in range(6000)]
    doc["facts"]["us-gaap"]["Bulk"] = {"label": "Bulk", "description": "", "units": {"USD": many}}
    sec.facts(doc)
    r = client.post(f"{API}/admin/statements/sync/EXMP")
    assert r.status_code == 200, r.text
    assert r.json()["facts_total"] == 9 + 6000


def test_watchlist_shows_a_name_for_a_stock_never_valued(client):
    """Owner's report: BRK-B added before any valuation showed its ticker twice. A name now comes from
    the security master (daily prices), the cached profile, or SEC's ticker list, and is remembered."""
    from backend.app.models import SecTicker, WatchlistItem
    from backend.app.routers.watchlist import company_name

    r = client.post(f"{API}/watchlist", json={"ticker": "EXMP"})
    assert r.status_code == 201
    rows = {w["ticker"]: w for w in client.get(f"{API}/watchlist").json()}
    assert rows["EXMP"]["company"] == "EXMP Inc."                 # from the security master (fake market source)
    with _db() as db:
        assert db.scalar(select(WatchlistItem.company).where(WatchlistItem.ticker == "EXMP")) == "EXMP Inc."
        db.add(SecTicker(ticker="ONLYSEC", cik="0000000009", title="Only Sec Corp"))
        db.flush()
        assert company_name(db, "ONLYSEC") == "Only Sec Corp"      # SEC's list when nothing else knows it
        assert company_name(db, "NOBODY") is None
