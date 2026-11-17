"""Tables. One valuation run has many method results; AI tasks are tracked per job."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    """A person who signs in. Roles: admin (users, branding, system settings) or member (decision D6)."""
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)     # stored lower-case
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="member")              # admin | member
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    prefs: Mapped[dict] = mapped_column(JSON, default=dict)                      # display currency, rebalance …
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """A signed-in browser (cookie) or app (bearer token). Only the SHA-256 of the token is stored."""
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8), default="cookie")               # cookie | token
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300))


class WatchlistItem(Base):
    """Private to each user (decision D10: valuation records are shared, watchlists are not)."""
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("user_id", "ticker", name="uq_watchlist_user_ticker"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    company: Mapped[Optional[str]] = mapped_column(String(200))
    added_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Run(Base):
    """One valuation attempt: done, failed or cancelled (all kept for the history page)."""
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    company: Mapped[Optional[str]] = mapped_column(String(200))
    run_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    valuation_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16), default="done")        # done | failed | cancelled
    trigger: Mapped[str] = mapped_column(String(32), default="manual")     # manual | new_filing | legacy …
    ai_mode: Mapped[str] = mapped_column(String(16), default="none")       # none | lite | standard | deep
    source: Mapped[str] = mapped_column(String(16), default="v2")          # v2 | legacy
    error: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[Optional[float]] = mapped_column(Float)
    price_date: Mapped[Optional[str]] = mapped_column(String(10))
    trading_ccy: Mapped[Optional[str]] = mapped_column(String(8))
    model_ccy: Mapped[Optional[str]] = mapped_column(String(8))
    composite_value: Mapped[Optional[float]] = mapped_column(Float)
    weights: Mapped[Optional[dict]] = mapped_column(JSON)
    wacc: Mapped[Optional[float]] = mapped_column(Float)
    terminal_g: Mapped[Optional[float]] = mapped_column(Float)
    verify_pass: Mapped[Optional[bool]] = mapped_column(Boolean)
    assumption_source: Mapped[Optional[str]] = mapped_column(String(32))
    ai_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    ai_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    ai_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    calc_json_path: Mapped[Optional[str]] = mapped_column(Text)
    xlsx_path: Mapped[Optional[str]] = mapped_column(Text)
    data_gaps: Mapped[Optional[list]] = mapped_column(JSON)
    requested_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    methods: Mapped[list["MethodResult"]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                         order_by="MethodResult.id")


class MethodResult(Base):
    """Per method and scenario, e.g. (dcf, base), (dcf, bear); relative / ddm arrive in phase 3."""
    __tablename__ = "method_results"
    __table_args__ = (UniqueConstraint("run_id", "method", "scenario"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(24))
    scenario: Mapped[str] = mapped_column(String(24))
    value: Mapped[Optional[float]] = mapped_column(Float)
    weight: Mapped[Optional[float]] = mapped_column(Float)
    run: Mapped[Run] = relationship(back_populates="methods")


class AiTask(Base):
    """What one AI task was expected to cost and what it actually cost."""
    __tablename__ = "ai_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), index=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    task: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))   # done | cached | skipped | failed | cancelled
    model: Mapped[Optional[str]] = mapped_column(String(80))
    est_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    est_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    usage_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Price(Base):
    """US$ per million tokens; editable in the settings page."""
    __tablename__ = "prices"
    model: Mapped[str] = mapped_column(String(80), primary_key=True)
    input: Mapped[float] = mapped_column(Float)
    cached_input: Mapped[Optional[float]] = mapped_column(Float)
    output: Mapped[float] = mapped_column(Float)
    note: Mapped[Optional[str]] = mapped_column(Text)
    checked_on: Mapped[Optional[str]] = mapped_column(String(10))
    source_url: Mapped[Optional[str]] = mapped_column(Text)
