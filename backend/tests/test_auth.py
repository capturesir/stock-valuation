"""Accounts and roles (decision D6): sign-in, sessions, private watchlists, admin-only settings."""
from __future__ import annotations

from conftest import ADMIN, MEMBER


def test_everything_but_health_and_sign_in_needs_a_session(client, client_as):
    anon = client_as()
    assert anon.get("/api/v1/health").status_code == 200
    assert anon.get("/api/v1/auth/status").json() == {"needs_setup": False}
    for path in ("/api/v1/watchlist", "/api/v1/auth/me", "/api/v1/settings/ai", "/api/v1/stocks/NVDA/valuations"):
        r = anon.get(path)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "login_required", path


def test_sign_in_sets_an_httponly_cookie_and_sign_out_ends_it(client, client_as):
    c = client_as()
    r = c.post("/api/v1/auth/login", json={"email": ADMIN[0].upper(), "password": ADMIN[1]})   # e-mail is case-blind
    assert r.status_code == 200 and r.json()["token"] is None
    cookie = r.headers["set-cookie"]
    assert "sv_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert c.get("/api/v1/auth/me").json()["role"] == "admin"
    assert c.post("/api/v1/auth/logout").status_code == 204
    c.cookies.clear()
    assert c.get("/api/v1/auth/me").status_code == 401


def test_apps_get_a_bearer_token(client, client_as):
    anon = client_as()
    r = anon.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": ADMIN[1], "issue_token": True})
    token = r.json()["token"]
    assert token and "set-cookie" not in r.headers
    assert anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_wrong_password_is_generic_and_locks_after_five_tries(client, client_as):
    anon = client_as()
    unknown = anon.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever-123"})
    assert unknown.status_code == 401 and unknown.json()["detail"]["code"] == "bad_credentials"
    for _ in range(5):
        r = anon.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": "wrong-password"})
    assert r.json()["detail"]["code"] == "bad_credentials"
    r = anon.post("/api/v1/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})     # right password, locked
    assert r.status_code == 429 and r.json()["detail"]["code"] == "account_locked"


def test_watchlists_are_private(client, member):
    client.post("/api/v1/watchlist", json={"ticker": "NVDA"})
    member.post("/api/v1/watchlist", json={"ticker": "0883.HK"})
    member.post("/api/v1/watchlist", json={"ticker": "NVDA"})          # same ticker, separate entry
    assert [w["ticker"] for w in client.get("/api/v1/watchlist").json()] == ["NVDA"]
    assert [w["ticker"] for w in member.get("/api/v1/watchlist").json()] == ["0883.HK", "NVDA"]
    assert member.delete("/api/v1/watchlist/NVDA").status_code == 204
    assert [w["ticker"] for w in client.get("/api/v1/watchlist").json()] == ["NVDA"]


def test_members_cannot_change_system_settings_or_users(client, member):
    assert member.get("/api/v1/settings/ai").status_code == 200        # reading is fine (key is masked)
    for method, path, body in (("put", "/api/v1/settings/ai", {"model": "x"}),
                               ("post", "/api/v1/settings/ai/test", None),
                               ("put", "/api/v1/settings/prices", {"model": "m", "input": 1, "output": 1}),
                               ("get", "/api/v1/admin/users", None),
                               ("post", "/api/v1/admin/users", {"email": "a@b.co", "password": "abcdefgh1"})):
        r = getattr(member, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "admin_required", path


def test_admin_manages_users_but_never_removes_the_last_admin(client, client_as):
    r = client.post("/api/v1/admin/users", json={"email": MEMBER[0], "password": MEMBER[1]})
    uid = r.json()["id"]
    assert r.json()["role"] == "member" and r.json()["display_name"] == "member"
    assert client.post("/api/v1/admin/users", json={"email": MEMBER[0], "password": MEMBER[1]}).status_code == 400
    assert client.post("/api/v1/admin/users", json={"email": "x@y.co", "password": "short"}).status_code == 400

    m = client_as(*MEMBER)
    assert m.get("/api/v1/auth/me").status_code == 200
    client.put(f"/api/v1/admin/users/{uid}", json={"is_active": False})       # disabling signs the user out
    assert m.get("/api/v1/auth/me").status_code == 401
    assert client_as().post("/api/v1/auth/login", json={"email": MEMBER[0], "password": MEMBER[1]}).json()[
        "detail"]["code"] == "account_disabled"

    me = client.get("/api/v1/auth/me").json()
    r = client.put(f"/api/v1/admin/users/{me['id']}", json={"role": "member"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "last_admin"


def test_password_change_keeps_this_device_and_signs_out_others(client, client_as):
    other = client_as(*ADMIN)
    r = client.put("/api/v1/auth/me/password", json={"current_password": "nope", "new_password": "new-pass-456"})
    assert r.json()["detail"]["code"] == "wrong_password"
    assert client.put("/api/v1/auth/me/password",
                      json={"current_password": ADMIN[1], "new_password": "new-pass-456"}).status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401


def test_preferences_hold_display_currency_and_rebalancing(client):
    me = client.get("/api/v1/auth/me").json()
    assert me["prefs"]["display_currency"] == "USD" and me["prefs"]["rebalance"] == "none"     # D7, D8 defaults
    r = client.put("/api/v1/auth/me", json={"prefs": {"display_currency": "HKD", "rebalance": "quarterly"}})
    assert r.json()["prefs"]["display_currency"] == "HKD" and r.json()["prefs"]["rebalance"] == "quarterly"
    assert client.put("/api/v1/auth/me", json={"prefs": {"display_currency": "hkd"}}).status_code == 422


def test_machine_token_acts_as_admin_but_has_no_watchlist(client, client_as, monkeypatch):
    from backend.app import config
    monkeypatch.setenv("APP_API_TOKEN", "s3cret-machine-token")
    config.reset_settings_cache()
    try:
        bot = client_as()
        h = {"Authorization": "Bearer s3cret-machine-token"}
        assert bot.get("/api/v1/admin/users", headers=h).status_code == 200
        assert bot.get("/api/v1/watchlist", headers=h).json()["detail"]["code"] == "user_required"
    finally:
        monkeypatch.delenv("APP_API_TOKEN")
        config.reset_settings_cache()


def test_first_admin_takes_over_watchlist_rows_from_before_accounts(client):
    from sqlalchemy import select

    from backend.app.auth import create_user
    from backend.app.db import session
    from backend.app.models import User, WatchlistItem

    with session() as db:
        for u in db.scalars(select(User)):
            db.delete(u)
        db.flush()
        db.add(WatchlistItem(ticker="NVDA", user_id=None))
        db.flush()
        boss = create_user(db, email="boss@example.com", display_name="", password="boss-pass-123", role="admin")
        assert db.scalar(select(WatchlistItem.user_id).where(WatchlistItem.ticker == "NVDA")) == boss.id
