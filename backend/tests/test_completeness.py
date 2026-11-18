"""Market-data completeness: calendar gaps, repair, source-missing days, stale series, monthly re-read,
dividends vs SEC (alpha.4 step 1)."""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, func, select


D = dt.date
NOW = dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc)      # Monday after END: the last final bar is END (Friday)


def _db():
    from backend.app.db import session
    return session()


@pytest.fixture(autouse=True)
def weekday_calendars(monkeypatch):
    """The fake source trades every weekday, so tests use a weekday calendar for every exchange."""
    from backend.app.services import completeness

    real = completeness.sessions
    monkeypatch.setattr(completeness, "sessions", lambda code, a, b: real(completeness.WEEKDAYS, a, b))


def _drop_source_days(fake, ticker, days):
    s = fake.data[ticker]
    keep = [i for i, d in enumerate(s.days) if d not in days]
    s.days, s.close, s.volume = [s.days[i] for i in keep], [s.close[i] for i in keep], [s.volume[i] for i in keep]


def test_complete_series(client, fake_market):
    from backend.app.services import completeness, market

    fake_market.add("FULL", start=D(2026, 9, 1))
    with _db() as db:
        market.sync(db, "FULL", now=NOW)
        r = completeness.check(db, "FULL", now=NOW)
    assert (r["status"], r["missing"], r["calendar"], r["sessions_behind"]) == ("complete", 0, "XNYS", 0)


def test_gaps_are_repaired_from_the_source(client, fake_market):
    from backend.app.models import PriceBar
    from backend.app.services import completeness, market

    fake_market.add("GAPS", start=D(2026, 9, 1))
    with _db() as db:
        market.sync(db, "GAPS", now=NOW)
        db.execute(delete(PriceBar).where(PriceBar.ticker == "GAPS", PriceBar.date.in_([D(2026, 10, 6), D(2026, 10, 7)])))
        db.commit()
        r = completeness.check(db, "GAPS", now=NOW)
        n = db.scalar(select(func.count()).select_from(PriceBar).where(PriceBar.ticker == "GAPS"))
    assert (r["found_missing"], r["repaired"], r["missing"], r["status"]) == (2, 2, 0, "complete")
    assert n == len(fake_market.data["GAPS"].days)
    assert ("GAPS", D(2026, 9, 29)) in fake_market.calls          # re-read from a week before the first gap


def test_days_the_source_does_not_have_are_recorded_never_filled(client, fake_market):
    from backend.app.models import PriceBar
    from backend.app.services import completeness, market

    fake_market.add("HOLE", start=D(2026, 9, 1))
    hole = [D(2026, 10, 12), D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 28)]
    _drop_source_days(fake_market, "HOLE", hole)
    with _db() as db:
        market.sync(db, "HOLE", now=NOW)
        r = completeness.check(db, "HOLE", now=NOW)
        stored = set(db.scalars(select(PriceBar.date).where(PriceBar.ticker == "HOLE")))
    assert r["status"] == "source_missing" and r["missing"] == 4 and r["repaired"] == 0
    assert r["segments"] == [["2026-10-12", "2026-10-14", 3], ["2026-10-28", "2026-10-28", 1]]
    assert not stored & set(hole)                                 # nothing interpolated


def test_a_series_that_stopped_updating_is_stale(client, fake_market):
    from backend.app.services import completeness, market

    fake_market.add("OLD", start=D(2026, 9, 1), end=D(2026, 10, 30))
    with _db() as db:
        market.sync(db, "OLD", now=NOW)
        r = completeness.check(db, "OLD", now=NOW)
    assert r["status"] == "stale" and r["sessions_behind"] == 10      # 2 Nov – 13 Nov


def test_monthly_reread_logs_silent_corrections(client, fake_market):
    from backend.app.models import PriceRevision, Security
    from backend.app.services import completeness, market

    s = fake_market.add("REREAD", start=D(2026, 9, 1))
    with _db() as db:
        market.sync(db, "REREAD", now=NOW)
    i = s.days.index(D(2026, 9, 15))
    s.close[i] = round(s.close[i] * 1.03, 4)                      # the source quietly changes an old close
    with _db() as db:
        r = completeness.reread(db, "REREAD", now=NOW)
        rev = db.scalars(select(PriceRevision).where(PriceRevision.ticker == "REREAD")).all()
        comp = db.get(Security, "REREAD").checks["completeness"]
    assert r["revised"] >= 1 and any(x.date == D(2026, 9, 15) and x.field == "close" for x in rev)
    assert comp["full_reread_at"] and comp["status"] == "complete"


def test_dividends_match_sec_on_todays_share_basis(client, fake_market):
    from decimal import Decimal

    from backend.app.models import FiledFactRow, Filer, RawDocumentRow, SecTicker
    from backend.app.services import completeness, market

    # quarterly 0.40 before a 4-for-1 split on 2025-06-02, then 0.10: the same payout on today's basis
    divs = {D(2023, 3, 1): 0.40, D(2023, 6, 1): 0.40, D(2023, 9, 1): 0.40, D(2023, 12, 1): 0.40,
            D(2024, 3, 1): 0.40, D(2024, 6, 3): 0.40, D(2024, 9, 3): 0.40, D(2024, 12, 2): 0.40,
            D(2025, 3, 3): 0.40, D(2025, 6, 2): 0.10, D(2025, 9, 2): 0.10, D(2025, 12, 1): 0.10}
    fake_market.add("DIVQ", start=D(2023, 1, 2), dividends=divs, splits={D(2025, 6, 2): 4})
    with _db() as db:
        market.sync(db, "DIVQ", now=NOW)
        db.add(Filer(cik="0000000042", name="DIVQ", facts=0))
        db.add(SecTicker(ticker="DIVQ", cik="0000000042", title="DIVQ"))
        raw = RawDocumentRow(source="sec", kind="companyfacts", key="0000000042", url="x", sha256="0" * 64, bytes=1,
                             content_type="application/json", path="x", http_status=200)
        db.add(raw)
        db.flush()
        for y, v, filed in ((2023, "1.60", D(2024, 2, 20)), (2024, "1.60", D(2025, 2, 20)), (2025, "0.55", D(2026, 2, 20))):
            db.add(FiledFactRow(cik="0000000042", taxonomy="us-gaap", concept="CommonStockDividendsPerShareDeclared",
                                unit="USD/shares", value=Decimal(v), period_start=D(y, 1, 1), period_end=D(y, 12, 31),
                                form="10-K", accession=f"A-{y}", filed=filed, raw_document_id=raw.id))
        db.flush()
        d = completeness.dividend_check(db, "DIVQ")
    # today's basis: 2023 0.40, 2024 0.40, 2025 0.10 + 0.30 = 0.40 (market) vs SEC 0.40, 0.40, 0.55 → 3y 1.20 vs 1.35
    assert d["status"] == "warn" and d["sec_3y"] == pytest.approx(1.35) and d["market_3y"] == pytest.approx(1.20)
    assert d["years"][-1]["concept"] == "CommonStockDividendsPerShareDeclared"


def test_daily_run_summarises(client, fake_market):
    from backend.app.db import session
    from backend.app.services import completeness, market

    fake_market.add("RUN1", start=D(2026, 9, 1))
    with _db() as db:
        market.sync(db, "RUN1", now=NOW)
    s = completeness.run_all(session, now=NOW)
    assert s["checked"] >= 1 and s["failed"] == [] and any(r["ticker"] == "RUN1" for r in s["reread"])


def test_admin_check_endpoint(client, member, fake_market):
    from backend.app.services import market

    fake_market.add("ADM", start=D(2026, 9, 1))
    with _db() as db:
        market.sync(db, "ADM")
    assert member.post("/api/v1/admin/market/check/ADM").status_code == 403
    r = client.post("/api/v1/admin/market/check/ADM")
    assert r.status_code == 200 and r.json()["status"] in ("complete", "stale")
    secs = {s["ticker"]: s for s in client.get("/api/v1/market/securities").json()}
    assert "completeness" in secs["ADM"]["checks"]
