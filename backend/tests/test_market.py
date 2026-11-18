"""Market data accumulation (roadmap v2.1 §4): unadjusted storage, total return, revisions, schedule."""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from market_fake import END

D = dt.date


def _db():
    from backend.app.db import session
    return session()


def test_split_adjusted_source_is_stored_unadjusted(client, fake_market):
    from backend.app.models import CorporateAction, PriceBar
    from backend.app.services import market

    s = fake_market.add("SPLT", start=D(2024, 6, 3), end=D(2024, 6, 14), base=1200.0, drift=0.0,
                        splits={D(2024, 6, 10): 10}, dividends={D(2024, 6, 11): 0.10})
    with _db() as db:
        r = market.sync(db, "SPLT")
        bars = {b.date: b for b in db.scalars(select(PriceBar).where(PriceBar.ticker == "SPLT"))}
        acts = {(a.date, a.kind): a.value for a in db.scalars(select(CorporateAction))}
    assert r["mode"] == "backfill" and r["bars_added"] == len(s.days)
    assert bars[D(2024, 6, 7)].close == pytest.approx(1200.0)          # as traded before the split
    assert bars[D(2024, 6, 10)].close == pytest.approx(120.0)          # and after it
    assert bars[D(2024, 6, 7)].volume == s.volume[s.days.index(D(2024, 6, 7))]
    assert acts[(D(2024, 6, 10), "split")] == 10 and acts[(D(2024, 6, 11), "dividend")] == pytest.approx(0.10)


def test_total_return_matches_the_source_adjusted_close(client, fake_market):
    from backend.app.models import Security
    from backend.app.services import market

    fake_market.add("DIVS", start=D(2023, 1, 2), base=80.0, splits={D(2024, 6, 10): 4},
                    dividends={D(2023, 6, 1): 0.9, D(2024, 3, 1): 1.1, D(2025, 3, 3): 0.35, D(2026, 3, 2): 0.4})
    with _db() as db:
        market.sync(db, "DIVS")
        check = db.get(Security, "DIVS").checks["total_return"]
        pts = market.series(db, "DIVS")
    assert check["status"] == "ok" and check["max_deviation"] < 1e-6
    assert pts[-1].adj_close == pytest.approx(pts[-1].close)            # latest adjusted = latest close
    first = fake_market.history("DIVS").bars[0]
    assert pts[0].adj_close == pytest.approx(first.adj_close, rel=1e-9)


def test_usd_dividend_error_in_the_source_is_explained(client, fake_market):
    """HSBC / MINISO: dividends declared in USD, traded in HKD. Yahoo's Adj Close deducts the USD
    amount from the HKD price; the system's figure is right and the gap is labelled as the source's."""
    from backend.app.models import Security
    from backend.app.services import market

    divs = {D(2024, 3, 1): 0.34, D(2025, 3, 3): 0.81, D(2026, 3, 2): 0.57}
    fake_market.add("9896.HK", start=D(2023, 1, 2), base=30.0, currency="HKD", tz="Asia/Hong_Kong",
                    dividends=divs, adj_divisor={D(2024, 3, 1): 7.80, D(2025, 3, 3): 7.83, D(2026, 3, 2): 7.79})
    with _db() as db:
        market.sync(db, "9896.HK")
        check = db.get(Security, "9896.HK").checks["total_return"]
    assert check["status"] == "explained" and check["note_code"] == "source_usd_dividend"
    assert check["max_deviation"] > 0.01 and check["residual_deviation"] <= 0.001
    assert [e["date"] for e in check["events"]] == ["2024-03-01", "2025-03-03", "2026-03-02"]
    assert check["events"][0]["source_applied"] == pytest.approx(0.34 / 7.80, rel=1e-3)


@pytest.mark.parametrize("case", ["usd_listing", "one_good_one_odd", "rights_issue"])
def test_other_mismatches_still_need_review(client, fake_market, case):
    from backend.app.models import Security
    from backend.app.services import market

    divs = {D(2024, 3, 1): 0.5, D(2025, 3, 3): 0.6}
    kw = {"usd_listing": dict(currency="USD", adj_divisor={D(2024, 3, 1): 7.8}),            # ratio fits, currency does not
          "one_good_one_odd": dict(currency="HKD", adj_divisor={D(2024, 3, 1): 7.8, D(2025, 3, 3): 2.0}),
          "rights_issue": dict(currency="HKD", adj_divisor={D(2024, 3, 1): 0.25})}[case]   # source deducts more
    fake_market.add("ODD", start=D(2023, 1, 2), base=20.0, dividends=divs, **kw)
    with _db() as db:
        market.sync(db, "ODD")
        check = db.get(Security, "ODD").checks["total_return"]
    assert check["status"] == "warn" and check["note_code"] == "adjusted_mismatch"


def test_unknown_symbol_is_not_kept(client, fake_market):
    """A mistyped code (TENCENT) used to stay tracked and fail in every daily update."""
    from backend.app import scheduler
    from backend.app.models import Security
    from backend.app.services import market

    fake_market.unknown.add("TENCENT")
    r = client.get("/api/v1/stocks/TENCENT/prices").json()
    assert r["points"] == [] and "TENCENT" in r["error"]
    with _db() as db:
        assert db.get(Security, "TENCENT") is None
        market.track(db, "TENCENT")                 # e.g. a row left by an older version
    summary = scheduler.run_once()
    assert [f["ticker"] for f in summary["failed"]] == ["TENCENT"]
    with _db() as db:
        assert db.get(Security, "TENCENT") is None


def test_a_symbol_with_history_keeps_it_when_the_source_drops_it(client, fake_market):
    from backend.app import scheduler
    from backend.app.models import Security
    from backend.app.services import market

    with _db() as db:
        market.sync(db, "GONE")
    fake_market.unknown.add("GONE")                 # delisted: keep what was accumulated, record why
    scheduler.run_once()
    with _db() as db:
        sec = db.get(Security, "GONE")
        assert sec is not None and sec.bars > 700 and "GONE" in sec.last_error


def test_not_found_does_not_open_the_source_breaker(monkeypatch):
    from src.fetch import guard
    from src.infra.kv import kv

    class YFTzMissingError(Exception):
        pass

    monkeypatch.setenv("APP_OUTBOUND_LIMITS", "0")
    for _ in range(8):
        with pytest.raises(YFTzMissingError):
            with guard.call("yahoo"):
                raise YFTzMissingError("$TENCENT: possibly delisted; no timezone found")
    assert not kv().get("cb:yahoo:open")


def test_update_appends_and_keeps_old_values_as_revisions(client, fake_market):
    from backend.app.models import PriceBar, PriceRevision, Security
    from backend.app.services import market

    s = fake_market.add("GROW", end=D(2026, 11, 6))
    with _db() as db:
        market.sync(db, "GROW")
    n0 = len(s.days)
    fake_market.add("GROW", end=END)                                   # one more week of trading
    fake_market.data["GROW"].close[-8] *= 1.02                         # the source corrects an old close
    with _db() as db:
        r = market.sync(db, "GROW")
        sec = db.get(Security, "GROW")
        revs = list(db.scalars(select(PriceRevision).where(PriceRevision.ticker == "GROW")))
        count = len(list(db.scalars(select(PriceBar).where(PriceBar.ticker == "GROW"))))
    assert r["mode"] == "update" and r["bars_added"] == 5 and r["bars_revised"] == 1
    assert fake_market.calls[-1][1] is not None                        # incremental: not the whole history again
    assert count == n0 + 5 and sec.last_date == END
    assert {r.date for r in revs} == {fake_market.data["GROW"].days[-8]}       # one bar, every field kept
    close = next(r for r in revs if r.field == "close")
    assert close.new == pytest.approx(close.old * 1.02)


def test_a_later_split_does_not_rewrite_stored_prices(client, fake_market):
    from backend.app.models import PriceBar, PriceRevision
    from backend.app.services import market

    fake_market.add("LATE", end=D(2026, 11, 6), base=500.0, drift=0.0)
    with _db() as db:
        market.sync(db, "LATE")
    fake_market.add("LATE", end=END, base=500.0, drift=0.0, splits={D(2026, 11, 10): 5})   # source re-adjusts history
    with _db() as db:
        r = market.sync(db, "LATE")
        before = db.get(PriceBar, ("LATE", D(2026, 11, 6))).close
        after = db.get(PriceBar, ("LATE", D(2026, 11, 10))).close
        revs = list(db.scalars(select(PriceRevision).where(PriceRevision.ticker == "LATE")))
    assert r["bars_revised"] == 0 and revs == []
    assert before == pytest.approx(500.0) and after == pytest.approx(100.0)


def test_today_is_stored_only_after_the_close(client, fake_market):
    from backend.app.models import Security
    from backend.app.services import market

    fake_market.add("NOWX", end=END)
    friday_noon_ny = dt.datetime(2026, 11, 13, 17, 0, tzinfo=dt.timezone.utc)      # 12:00 New York
    with _db() as db:
        market.sync(db, "NOWX", now=friday_noon_ny)
        assert db.get(Security, "NOWX").last_date == D(2026, 11, 12)
        market.sync(db, "NOWX", now=friday_noon_ny + dt.timedelta(hours=6))         # 18:00 New York
        assert db.get(Security, "NOWX").last_date == END


def test_prices_endpoint_backfills_on_first_use(client, fake_market):
    r = client.get("/api/v1/stocks/0883.HK/prices?days=90").json()
    assert r["currency"] == "HKD" and r["error"] is None
    assert r["coverage"]["first_date"] == "2024-01-01" and r["coverage"]["bars"] > 700
    assert 55 <= len(r["points"]) <= 70 and set(r["points"][0]) == {"date", "close", "adj_close"}
    calls = len(fake_market.calls)
    client.get("/api/v1/stocks/0883.HK/prices?days=30")
    assert len(fake_market.calls) == calls                              # fresh: served from the database


def test_prices_endpoint_reports_a_source_failure(client, fake_market):
    fake_market.fail.add("DOWN")
    r = client.get("/api/v1/stocks/DOWN/prices").json()
    assert r["points"] == [] and "source down" in r["error"]
    sec = client.get("/api/v1/market/securities").json()
    assert sec[0]["ticker"] == "DOWN" and "source down" in sec[0]["last_error"]


def test_watchlist_add_starts_accumulating(client, fake_market):
    client.post("/api/v1/watchlist", json={"ticker": "QQQ"})
    sec = {s["ticker"]: s for s in client.get("/api/v1/market/securities").json()}
    assert sec["QQQ"]["bars"] > 700 and sec["QQQ"]["checks"]["total_return"]["status"] in ("ok", "skipped")


def test_daily_update_syncs_everything_tracked_and_adds_fx(client, fake_market):
    from backend.app import scheduler
    from backend.app.services import market

    for t in ("SPY", "2800.HK", "BAD"):
        with _db() as db:
            market.track(db, t)
    with _db() as db:
        market.sync(db, "2800.HK")               # currency becomes known (HKD)
    fake_market.fail.add("BAD")
    summary = scheduler.run_once()
    assert {f["ticker"] for f in summary["failed"]} == {"BAD"}
    status = client.get("/api/v1/market/status").json()
    assert status["last_run"]["synced"] == summary["synced"] and status["scheduler"] == "off"
    tickers = {s["ticker"]: s for s in client.get("/api/v1/market/securities").json()}
    assert "HKDUSD=X" in tickers and tickers["HKDUSD=X"]["bars"] > 0
    assert "source down" in tickers["BAD"]["last_error"]
    with _db() as db:
        rates = market.fx_rates(db, "USD", "HKD")
    assert rates and abs(next(iter(rates.values())) - 7.8) < 1e-9


def test_market_admin_routes_are_admin_only(client, member, fake_market):
    assert member.get("/api/v1/market/status").status_code == 200
    assert member.post("/api/v1/admin/market/sync/SPY").status_code == 403
    assert member.post("/api/v1/admin/market/update-all").status_code == 403
    r = client.post("/api/v1/admin/market/sync/SPY?full=true").json()
    assert r["mode"] == "backfill" and r["bars"] > 700


def test_schedule_runs_after_both_closes():
    from backend.app import scheduler

    assert scheduler.times() == [(6, 30), (17, 30)]
    assert sorted(scheduler.utc_cron()) == [(9, 30), (22, 30)]          # Asia/Macau is UTC+8
    nxt = scheduler.next_runs(dt.datetime(2026, 11, 18, 7, 0, tzinfo=dt.timezone.utc))   # 15:00 Macau
    assert [t.strftime("%H:%M") for t in nxt] == ["17:30", "06:30"]
