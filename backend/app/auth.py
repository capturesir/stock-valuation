"""Accounts: password hashing, sessions and the principal behind every request (decision D6).

- Browsers sign in with a cookie (httpOnly, SameSite=Lax); apps and mini programs use the same
  session as a bearer token. Only the SHA-256 of a token is stored.
- ``APP_API_TOKEN`` remains a machine credential for scripts and schedulers: it acts with admin
  rights but is not a person, so it has no watchlist or preferences.
- Roles: ``admin`` manages users, branding and system settings; ``member`` uses the system.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Header, Query, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .i18n import AppError, http_error, lang_dep
from .models import AuthSession, User, WatchlistItem

COOKIE = "sv_session"
SESSION_DAYS = 30
TOUCH_EVERY = dt.timedelta(minutes=5)       # how often last_seen_at is refreshed
MAX_FAILED = 5                              # failed sign-ins before a temporary lock
LOCK_FOR = dt.timedelta(minutes=15)
ROLES = ("admin", "member")
MIN_PASSWORD = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_hasher = PasswordHasher()
# verifying against this keeps the response time the same whether or not the e-mail exists
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(ts: Optional[dt.datetime]) -> Optional[dt.datetime]:
    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ passwords and users

def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not EMAIL_RE.match(e) or len(e) > 254:
        raise AppError("email_invalid")
    return e


def check_password_rules(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AppError("password_short", n=MIN_PASSWORD)
    if len(set(password)) < 3:
        raise AppError("password_weak")


def hash_password(password: str) -> str:
    check_password_rules(password)
    return _hasher.hash(password)


def verify_password(user: Optional[User], password: str) -> bool:
    try:
        ok = _hasher.verify(user.password_hash if user else _DUMMY_HASH, password or "")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return ok and user is not None


def active_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))) or 0


def create_user(db: Session, *, email: str, display_name: str, password: str, role: str = "member") -> User:
    if role not in ROLES:
        raise AppError("role_invalid")
    e = normalize_email(email)
    if db.scalar(select(User).where(User.email == e)) is not None:
        raise AppError("email_taken", email=e)
    name = (display_name or "").strip() or e.split("@")[0]
    first_admin = role == "admin" and active_admins(db) == 0
    user = User(email=e, display_name=name[:80], password_hash=hash_password(password), role=role,
                is_active=True, prefs={})
    db.add(user)
    db.flush()
    if first_admin:
        # watchlist rows from before accounts existed (2.0.0-alpha.1) go to the first admin
        for w in db.scalars(select(WatchlistItem).where(WatchlistItem.user_id.is_(None))):
            w.user_id = user.id
        db.flush()
    return user


def revoke_sessions(db: Session, user_id: str, keep: Optional[str] = None) -> None:
    q = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep:
        q = q.where(AuthSession.token_hash != keep)
    db.execute(q)


# ------------------------------------------------------------------ sessions

def authenticate(db: Session, email: str, password: str) -> tuple[Optional[User], Optional[str]]:
    """Returns (user, None) or (None, error_code). Locks an account after repeated failures."""
    try:
        e = normalize_email(email)
    except ValueError:
        verify_password(None, password)
        return None, "bad_credentials"
    user = db.scalar(select(User).where(User.email == e))
    now = _now()
    if user is not None and _aware(user.locked_until) and _aware(user.locked_until) > now:
        return None, "account_locked"
    if not verify_password(user, password):
        if user is not None:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= MAX_FAILED:
                user.locked_until, user.failed_logins = now + LOCK_FOR, 0
        return None, "bad_credentials"
    if not user.is_active:
        return None, "account_disabled"
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    return user, None


def start_session(db: Session, user: User, kind: str = "cookie", user_agent: Optional[str] = None) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    db.add(AuthSession(token_hash=_token_hash(token), user_id=user.id, kind=kind, created_at=now,
                       expires_at=now + dt.timedelta(days=SESSION_DAYS), last_seen_at=now,
                       user_agent=(user_agent or "")[:300] or None))
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now))    # housekeeping
    return token


def end_session(db: Session, token: Optional[str]) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))


def set_cookie(response: Response, request: Request, token: str) -> None:
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=https, path="/")


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


# ------------------------------------------------------------------ who is calling

@dataclass
class Principal:
    user: Optional[User]          # None for the machine token
    session_hash: Optional[str] = None

    @property
    def role(self) -> str:
        return self.user.role if self.user else "service"

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "service")


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def current_principal(request: Request, authorization: Optional[str] = Header(default=None),
                      access_token: Optional[str] = Query(default=None, include_in_schema=False),
                      db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> Principal:
    """Every /api/v1 route except health, sign-in and public branding depends on this.
    ``access_token`` is accepted because browser EventSource (SSE) cannot send headers."""
    token = _bearer(authorization) or access_token or request.cookies.get(COOKIE)
    if not token:
        raise http_error(401, "login_required", lang)
    machine = get_settings().api_token
    if machine and hmac.compare_digest(token, machine):
        return Principal(user=None)
    h = _token_hash(token)
    sess = db.get(AuthSession, h)
    now = _now()
    if sess is None or _aware(sess.expires_at) <= now:
        raise http_error(401, "login_required", lang)
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise http_error(401, "login_required", lang)
    if now - _aware(sess.last_seen_at) > TOUCH_EVERY:
        sess.last_seen_at = now
    return Principal(user=user, session_hash=h)


def require_user(p: Principal = Depends(current_principal), lang: str = Depends(lang_dep)) -> User:
    """Routes that belong to a person (watchlist, preferences)."""
    if p.user is None:
        raise http_error(403, "user_required", lang)
    return p.user


def require_admin(p: Principal = Depends(current_principal), lang: str = Depends(lang_dep)) -> Principal:
    if not p.is_admin:
        raise http_error(403, "admin_required", lang)
    return p
