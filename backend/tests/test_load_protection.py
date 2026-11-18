"""P0 load protection: per-user limits, valuation queue policy, single-flight, outbound pacing/breaker."""
from __future__ import annotations

import threading
import time

import pytest

from test_api import _install_fake_engine, _wait


def test_lookup_limit_answers_429_with_retry_after(client, monkeypatch):
    from backend.app.services import profile as prof
    monkeypatch.setattr(prof, "_build", lambda t: {"ticker": t, "name": "X", "source": "Yahoo Finance"})
    client.put("/api/v1/admin/limits", json={"lookup_per_minute": 3})
    codes = [client.get("/api/v1/stocks/NVDA/profile").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.get("/api/v1/stocks/NVDA/profile", headers={"Accept-Language": "en"})
    d = r.json()["detail"]
    assert d["code"] == "rate_limited" and 1 <= d["retry_after"] <= 60 and r.headers["retry-after"] == str(d["retry_after"])
    assert "Try again in" in d["error"]


def test_limits_are_per_user(client, member, monkeypatch):
    from backend.app.services import profile as prof
    monkeypatch.setattr(prof, "_build", lambda t: {"ticker": t, "name": "X", "source": "Yahoo Finance"})
    client.put("/api/v1/admin/limits", json={"lookup_per_minute": 1})
    assert client.get("/api/v1/stocks/NVDA/profile").status_code == 200
    assert client.get("/api/v1/stocks/NVDA/profile").status_code == 429
    assert member.get("/api/v1/stocks/NVDA/profile").status_code == 200        # own budget


def test_only_admins_change_limits_and_values_are_clamped(client, member):
    assert member.put("/api/v1/admin/limits", json={"queue_max": 5}).status_code == 403
    r = client.put("/api/v1/admin/limits", json={"revalue_cooldown_hours": 9999, "queue_max": 5}).json()
    assert r["revalue_cooldown_hours"] == 168 and r["queue_max"] == 5 and r["defaults"]["queue_max"] == 20


def test_recent_identical_valuation_asks_before_rerunning(configured, monkeypatch):
    _install_fake_engine(monkeypatch)
    _wait(configured, configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"}).json()["id"])
    r = configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "recent_valuation"
    assert r.json()["detail"]["run_id"]
    # another AI mode is a different question: no prompt
    lite = configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "lite"})
    assert lite.status_code == 202
    _wait(configured, lite.json()["id"])


def test_one_running_valuation_per_user_and_a_queue_cap(configured, monkeypatch):
    _install_fake_engine(monkeypatch, slow=True)
    first = configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"})
    r = configured.post("/api/v1/valuations", json={"ticker": "NVDA", "mode": "none"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "too_many_jobs"
    configured.put("/api/v1/admin/limits", json={"concurrent_valuations_per_user": 5, "queue_max": 1})
    r = configured.post("/api/v1/valuations", json={"ticker": "NVDA", "mode": "none"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "queue_full"
    _wait(configured, first.json()["id"])


def test_valuation_starts_per_hour(configured, monkeypatch):
    _install_fake_engine(monkeypatch)
    configured.put("/api/v1/admin/limits", json={"valuations_per_hour": 2})
    for t in ("A1", "A2"):
        _wait(configured, configured.post("/api/v1/valuations", json={"ticker": t, "mode": "none"}).json()["id"])
    r = configured.post("/api/v1/valuations", json={"ticker": "A3", "mode": "none"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"


def test_concurrent_lookups_of_one_ticker_hit_the_source_once(client, monkeypatch):
    from backend.app.services import profile as prof
    calls = []

    def slow_build(t):
        calls.append(t)
        time.sleep(0.3)
        return {"ticker": t, "name": "Tencent", "source": "Yahoo Finance"}
    monkeypatch.setattr(prof, "_build", slow_build)
    from backend.app.db import session
    results = []

    def worker():
        with session() as db:
            results.append(prof.profile(db, "0700.HK")["name"])
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results == ["Tencent"] * 8 and calls == ["0700.HK"]


def test_source_outage_shows_stale_profile_not_not_found(client, monkeypatch):
    from backend.app import cache
    from backend.app.services import profile as prof
    cache.put("profile:0700.HK", {"ticker": "0700.HK", "name": "Tencent", "source": "Yahoo Finance"}, ttl=0.01,
              stale_ttl=3600)
    time.sleep(0.05)

    def down(t):
        raise prof.Unavailable("Yahoo 暫停中", 42)
    monkeypatch.setattr(prof, "fetch_quote", down)
    p = client.get("/api/v1/stocks/0700.HK/profile").json()
    assert p["name"] == "Tencent"
    r = client.get("/api/v1/stocks/9999.HK/profile")                  # nothing stored: say the source is down
    assert r.status_code == 503 and r.json()["detail"]["code"] == "upstream_unavailable"


def test_outbound_breaker_opens_after_repeated_failures(monkeypatch):
    from src.fetch import guard
    from src.infra import kv
    kv.reset()
    monkeypatch.setenv("APP_OUTBOUND_LIMITS", "0")
    for _ in range(guard.POLICIES["yahoo"].threshold):
        guard.record_status("yahoo", 503)
    with pytest.raises(guard.ProviderUnavailable):
        guard.check_open("yahoo")
    assert guard.status()["yahoo"]["open"] is True
    guard.record_status("sec", 404)                                   # "not found" is an answer, not a failure
    assert guard.status()["sec"]["recent_failures"] == 0


def test_outbound_pacing_spaces_calls(monkeypatch):
    from src.fetch import guard
    from src.infra import kv
    kv.reset()
    monkeypatch.setenv("APP_OUTBOUND_LIMITS", "1")
    monkeypatch.setenv("APP_OUTBOUND_OTHER_INTERVAL", "0.1")
    t0 = time.monotonic()
    for _ in range(4):
        guard.acquire("other")
    assert time.monotonic() - t0 >= 0.28                              # 3 gaps of 0.1 s


def test_httputil_fails_fast_while_the_breaker_is_open(monkeypatch):
    from src.fetch import httputil
    from src.infra import kv
    kv.reset()
    kv.kv().set("cb:yahoo:open", "1", ttl=30)
    import json
    status, body, _ = httputil.get("https://query1.finance.yahoo.com/v8/finance/chart/NVDA")
    assert status == 0 and "暫時無法使用" in json.loads(body)["error"]
