"""Statement data layers ① raw and ② filed: SEC sync, accumulation, restatements, reading (offline)."""
from __future__ import annotations

import gzip
from decimal import Decimal
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
    assert r.status_code == 200 and r.json() == {**SOURCES, "sec_contact": CONTACT, "sec_ready": True, "statements_last_run": None}
    return fake


def _db():
    from backend.app.db import session
    return session()


def test_data_sources_are_admin_only_and_validated(client, member):
    empty = {"sec_organisation": None, "sec_email": None, "sec_contact": None, "sec_ready": False, "statements_last_run": None}
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
    assert r.json() == {**SOURCES, "sec_contact": CONTACT, "sec_ready": True, "statements_last_run": None}          # spaces tidied
    assert client.put(f"{API}/admin/data-sources", json={"sec_organisation": "", "sec_email": ""}).json() == empty


def test_an_older_single_string_contact_is_read_as_two_fields(client):
    from backend.app.models import Setting

    with _db() as db:
        db.add(Setting(key="data_sources", value={"sec_contact": CONTACT}))
        db.commit()
    assert client.get(f"{API}/admin/data-sources").json() == {**SOURCES, "sec_contact": CONTACT, "sec_ready": True, "statements_last_run": None}


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


def test_standardised_annual_statements_with_lineage(client, sec, member):
    assert member.get(f"{API}/stocks/EXMP/statements").json()["detail"]["code"] == "statements_not_synced"
    client.post(f"{API}/admin/statements/sync/EXMP")
    d = member.get(f"{API}/stocks/EXMP/statements").json()
    assert (d["template"], d["currency"], d["view"], d["ruleset"]) == ("general", "USD", "latest", "general")
    assert [y["label"] for y in d["years"]] == ["FY2024", "FY2023"]                      # newest first
    rows = {r["code"]: r for r in d["rows"]}
    rev24, rev23 = rows["revenue"]["cells"]
    assert rev24["value"] == "130497000000" and rev24["concept"] == "us-gaap:Revenues" and rev24["form"] == "10-K"
    assert rev24["document_url"].endswith("/1234567/000123456725000010/exmp-20241231.htm")
    # FY2023 was restated in the FY2024 10-K: latest view shows the new number, and says so
    assert (rev23["value"], rev23["change"], rev23["original_value"]) == ("60900000000", "restated", "60922000000")
    assert rows["eps_diluted"]["cells"][0]["value"] == "2.94"
    assert rows["total_assets"]["cells"][0]["value"] == "111601000000" and rows["total_assets"]["cells"][1]["value"] == "65728000000"
    assert rows["research_development"]["cells"] == [None, None]                          # not filed: empty, never guessed
    first = member.get(f"{API}/stocks/EXMP/statements", params={"view": "original"}).json()
    assert {r["code"]: r for r in first["rows"]}["revenue"]["cells"][1]["value"] == "60922000000"
    then = member.get(f"{API}/stocks/EXMP/statements", params={"as_of": "2024-12-31"}).json()
    assert [y["label"] for y in then["years"]] == ["FY2023"]                              # the FY2024 10-K was not filed yet
    assert member.get(f"{API}/stocks/EXMP/statements", params={"view": "guess"}).status_code == 422


# ------------------------------------------------------------------ step 3: everyone can fetch, daily check

def test_any_user_can_fetch_once_the_sec_contact_is_set(client, member, monkeypatch):
    from backend.app.services import statements

    fake = FakeSec()
    monkeypatch.setattr(statements, "_http", fake)
    r = member.post(f"{API}/stocks/EXMP/statements/fetch")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "sec_contact_missing"
    client.put(f"{API}/admin/data-sources", json=SOURCES)
    st = member.get(f"{API}/stocks/EXMP/statements/status").json()
    assert (st["sec_ready"], st["us_filer"], st["synced"], st["pending"]) == (True, None, False, False)
    r = member.post(f"{API}/stocks/EXMP/statements/fetch")
    assert r.status_code == 202, r.text                      # runs in the background (inline in tests)
    st = member.get(f"{API}/stocks/EXMP/statements/status").json()
    assert (st["us_filer"], st["synced"], st["facts"], st["pending"]) == (True, True, 9, False)
    calls = len(fake.calls)
    r = member.post(f"{API}/stocks/EXMP/statements/fetch")
    assert r.status_code == 200 and len(fake.calls) == calls  # fetched within 6 hours: not repeated
    r = member.post(f"{API}/stocks/0700.HK/statements/fetch")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "sec_not_filer"


def test_a_failed_background_fetch_is_reported_in_the_status(client, sec, member):
    sec.routes[sec_source.SUBMISSIONS_URL.format(name=f"CIK{CIK}.json")] = (503, b"busy")
    assert member.post(f"{API}/stocks/EXMP/statements/fetch").status_code == 202
    st = member.get(f"{API}/stocks/EXMP/statements/status").json()
    assert not st["synced"] and st["last_error"]


def test_adding_a_us_stock_to_the_watchlist_fetches_its_statements(client, sec):
    from backend.app.db import session
    from backend.app.services import statements

    with session() as db:                                    # SEC's ticker list is known (e.g. from an earlier fetch)
        c = statements.client(db)
        statements.refresh_tickers(db, c)
    assert client.post(f"{API}/watchlist", json={"ticker": "EXMP"}).status_code == 201
    st = client.get(f"{API}/stocks/EXMP/statements/status").json()
    assert st["synced"] and st["facts"] == 9
    client.post(f"{API}/watchlist", json={"ticker": "0700.HK"})       # not an SEC filer: nothing is sent
    assert not any("0700" in u for u in sec.calls)


def test_daily_check_downloads_facts_only_after_a_new_filing(client, sec):
    from backend.app.db import session
    from backend.app.services import statements

    client.post(f"{API}/admin/statements/sync/EXMP")
    sec.calls.clear()
    s = statements.update_all(session)
    assert s["checked"] == 1 and s["updated"] == [] and s["failed"] == []
    assert not any("companyfacts" in u for u in sec.calls)            # no new filing: one small request only
    # a new 10-Q appears in the filing list
    sub = json.loads((FIX / "submissions.json").read_text())
    recent = sub["filings"]["recent"]
    for k, v in {"accessionNumber": "0001234567-25-000030", "filingDate": "2025-05-01", "reportDate": "2025-03-31",
                 "acceptanceDateTime": "2025-05-01T16:00:00.000Z", "form": "10-Q", "primaryDocument": "exmp-20250331.htm",
                 "primaryDocDescription": "10-Q", "items": "", "isXBRL": 1, "isInlineXBRL": 1}.items():
        recent[k].insert(0, v)
    sec.routes[sec_source.SUBMISSIONS_URL.format(name=f"CIK{CIK}.json")] = (200, json.dumps(sub).encode())
    sec.calls.clear()
    s = statements.update_all(session)
    assert s["updated"] == [{"ticker": "EXMP", "filings_added": 1, "facts_added": 0}]
    assert any("companyfacts" in u for u in sec.calls)
    with session() as db:
        from backend.app.models import Setting
        assert db.get(Setting, "statements_status").value["checked"] == 1


def test_daily_check_is_skipped_without_a_sec_contact(client):
    from backend.app.db import session
    from backend.app.services import statements

    assert statements.update_all(session)["skipped"] == "sec_contact_missing"


def test_quarterly_statements_with_derived_q4_and_ttm(client, sec, member):
    doc = json.loads((FIX / "companyfacts.json").read_text())
    rev = doc["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    q = lambda s, e, v, accn, filed: {"start": s, "end": e, "val": v, "accn": accn, "fy": 2024, "fp": "Q", "form": "10-Q", "filed": filed}
    rev += [q("2024-01-01", "2024-03-31", 30000000000, "0001234567-24-000010", "2024-05-01"),
            q("2024-04-01", "2024-06-30", 34000000000, "0001234567-24-000015", "2024-08-01"),
            q("2024-01-01", "2024-06-30", 64000000000, "0001234567-24-000015", "2024-08-01"),
            q("2024-01-01", "2024-09-30", 99082000000, "0001234567-24-000020", "2024-11-01"),
            # the year in progress: FY2025 Q1, so four consecutive quarters exist for TTM
            {**q("2025-01-01", "2025-03-31", 40000000000, "0001234567-25-000030", "2025-05-01"), "fy": 2025}]
    sec.facts(doc)
    client.post(f"{API}/admin/statements/sync/EXMP")
    d = member.get(f"{API}/stocks/EXMP/statements", params={"period": "quarterly"}).json()
    assert d["period"] == "quarterly"
    labels = [y["label"] for y in d["years"]]
    assert labels == ["TTM", "FY2025 Q1", "FY2024 Q4", "FY2024 Q3", "FY2024 Q2", "FY2024 Q1"]
    assert d["years"][0]["kind"] == "ttm" and d["years"][2] == {"label": "FY2024 Q4", "start": "2024-10-01", "end": "2024-12-31",
                                                                 "kind": "quarter", "fiscal_year": "FY2024", "quarter": 4}
    cells = dict(zip(labels, {r["code"]: r for r in d["rows"]}["revenue"]["cells"]))
    q4 = cells["FY2024 Q4"]
    assert (q4["value"], q4["method"]) == ("31415000000", "derived")            # FY 130,497 − 9M 99,082
    assert [(p["sign"], p["accession"]) for p in q4["parts"]] == [(1, "0001234567-25-000010"), (-1, "0001234567-24-000020")]
    assert cells["FY2024 Q3"]["value"] == "35082000000" and cells["FY2024 Q3"]["method"] == "concept"
    assert cells["TTM"]["value"] == str(34000000000 + 35082000000 + 31415000000 + 40000000000) and cells["TTM"]["method"] == "ttm"
    annual = member.get(f"{API}/stocks/EXMP/statements").json()
    assert [y["label"] for y in annual["years"]] == ["TTM", "FY2024", "FY2023"]  # TTM leads the annual view too


# ------------------------------------------------------------------ step 5: checks layer

def test_checks_and_grades_with_a_yahoo_cross_check(client, sec, member, monkeypatch):
    from backend.app.models import RawDocumentRow
    from backend.app.services import statements

    yahoo = {"ticker": "EXMP", "annual": {"2024-12-31": {"Total Revenue": 130497000000.0, "Total Assets": 111601000000.0},
                                          "2023-12-31": {"Total Revenue": 61000000000.0}}}       # 0.16 % off: within 1 %
    monkeypatch.setattr(statements, "_yahoo_annual", lambda t: yahoo)
    client.post(f"{API}/admin/statements/sync/EXMP")
    with _db() as db:
        assert db.scalar(select(func.count()).select_from(RawDocumentRow).where(RawDocumentRow.source == "yahoo")) == 1
    d = member.get(f"{API}/stocks/EXMP/statements").json()
    c = d["checks"]
    assert c["checkset"] == "general" and c["yahoo_fetched_at"]
    assert c["grades"] == {"FY2024": "A", "FY2023": "A"} and c["failures"] == []
    assert c["summary"]["FY2024"]["pass"] >= 2                 # Yahoo revenue and total assets agree
    # Yahoo disagrees by 5 %: a warning, grade B — the SEC number is still the one shown
    yahoo["annual"]["2024-12-31"]["Total Revenue"] = 124000000000.0
    with _db() as db:
        statements.refresh_yahoo(db, "EXMP", force=True)
    d = member.get(f"{API}/stocks/EXMP/statements").json()
    f = d["checks"]["failures"]
    assert d["checks"]["grades"]["FY2024"] == "B"
    assert [(x["rule_id"], x["period"], x["severity"], x["values"]["item"]) for x in f] == [("yahoo_matches", "FY2024", "warning", "revenue")]
    assert {r["code"]: r for r in d["rows"]}["revenue"]["cells"][0]["value"] == "130497000000"


def test_checks_without_a_second_source(client, sec, member):
    client.post(f"{API}/admin/statements/sync/EXMP")
    c = member.get(f"{API}/stocks/EXMP/statements").json()["checks"]
    assert c["yahoo_fetched_at"] is None and all(g in ("A", "B", "C") for g in c["grades"].values())


def test_banks_get_the_bank_chart_and_checks(client, sec, member):
    sub = json.loads((FIX / "submissions.json").read_text())
    sub["sic"], sub["sicDescription"] = "6021", "National Commercial Banks"
    sec.routes[sec_source.SUBMISSIONS_URL.format(name=f"CIK{CIK}.json")] = (200, json.dumps(sub).encode())
    doc = json.loads((FIX / "companyfacts.json").read_text())
    g = doc["facts"]["us-gaap"]
    k = {"accn": "0001234567-25-000010", "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-20"}
    for concept, v in {"InterestIncomeOperating": 193341000000, "InterestExpenseOperating": 97898000000,
                       "InterestIncomeExpenseNet": 95443000000, "NoninterestIncome": 87004000000}.items():
        g[concept] = {"label": concept, "description": "", "units": {"USD": [{"start": "2024-01-01", "end": "2024-12-31", "val": v, **k}]}}
    g["Deposits"] = {"label": "Deposits", "description": "", "units": {"USD": [{"end": "2024-12-31", "val": 2559320000000, **k}]}}
    sec.facts(doc)
    client.post(f"{API}/admin/statements/sync/EXMP")
    d = member.get(f"{API}/stocks/EXMP/statements").json()
    assert (d["template"], d["template_applied"], d["checks"]["checkset"]) == ("bank", "bank", "bank")
    rows = {r["code"]: r for r in d["rows"]}
    assert rows["net_interest_income"]["cells"][0]["value"] == "95443000000" and rows["net_interest_income"]["total"]
    assert [r["code"] for r in d["rows"] if r["statement"] == "income"][:3] == ["interest_income", "interest_expense", "net_interest_income"]
    assert "gross_profit" not in rows                                            # not a bank item
    fails = {(x["rule_id"], x["period"]) for x in d["checks"]["failures"]}
    assert ("net_interest_income", "FY2024") not in fails                        # 193,341 − 97,898 = 95,443
    assert ("deposits_within_liabilities", "FY2024") not in fails                # no total liabilities filed here: skipped, not failed


# ------------------------------------------------------------------ step 7: ratios, growth, export

def test_ratios_growth_and_their_inputs(client, sec, member):
    client.post(f"{API}/admin/statements/sync/EXMP")
    d = member.get(f"{API}/stocks/EXMP/statements").json()
    r = d["ratios"]
    assert r["ratioset"] == "general" and r["version"]
    labels = [y["label"] for y in d["years"]]
    growth = {g["code"]: dict(zip(labels, g["cells"])) for g in r["growth"]}
    # FY2024 revenue 130,497 vs restated FY2023 60,900 (latest view)
    assert growth["revenue"]["FY2024"] == str((Decimal("130497") / Decimal("60900") - 1).quantize(Decimal("0.0001")))
    assert growth["revenue"]["FY2023"] is None                 # no FY2022 to compare with
    ratios = {x["code"]: dict(zip(labels, x["cells"])) for x in r["rows"]}
    roa = ratios["roa"]["FY2024"]
    assert roa is None                                         # no net income filed in the fixture
    assert ratios["liabilities_to_assets"]["FY2024"] is None   # no liabilities filed


def test_export_excel_and_csv(client, sec, member):
    from openpyxl import load_workbook

    client.post(f"{API}/admin/statements/sync/EXMP")
    r = member.get(f"{API}/stocks/EXMP/statements/export", params={"format": "csv"}, headers={"Accept-Language": "en"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="EXMP_annual_' in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")
    lines = text.replace("\r", "").splitlines()
    assert lines[1].startswith("Item,code,Unit,FY2024 (2024-12-31),FY2023 (2023-12-31)")
    assert "Revenue,revenue,USD,130497000000,60900000000" in lines
    r = member.get(f"{API}/stocks/EXMP/statements/export", params={"format": "xlsx"})
    assert r.status_code == 200 and 'filename="EXMP_annual_' in r.headers["content-disposition"]
    import io
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["標準化財報", "比率", "出處", "檢查"]
    ws = wb["標準化財報"]
    assert ws["A1"].value == "EXAMPLE CORP (EXMP)" and ws["A2"].value == "估值工作台"      # the system name (branding)
    header = [c.value for c in ws[5]]
    found = [row for row in ws.iter_rows(values_only=True) if row[0] == "營業收入"]
    assert header[1].startswith("FY2024") and found[0][1] == 130497000000     # exact, full units
    lineage = [row for row in wb["出處"].iter_rows(values_only=True) if row[0] == "營業收入" and row[1] == "FY2024"]
    assert lineage[0][5] == "us-gaap:Revenues" and lineage[0][7] == "0001234567-25-000010"
