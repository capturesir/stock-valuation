"""Tables. One valuation run has many method results; AI tasks are tracked per job."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint)
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
    # alpha.3 step 10: where the statements came from and which adjustment plan was applied (D12)
    statement_source: Mapped[Optional[str]] = mapped_column(String(16))   # sec_standard | yahoo | builtin
    ruleset_version: Mapped[Optional[str]] = mapped_column(String(48))    # standard-layer ruleset, with company overlay
    adjustment_plan: Mapped[Optional[dict]] = mapped_column(JSON)          # {kind, id, version, label}; null = system default
    plan_key: Mapped[str] = mapped_column(String(80), default="system_default", server_default="system_default")
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


# ---------------------------------------------------------------- market data (§4 of the v2.1 roadmap)

class Security(Base):
    """Anything with a daily price series: stocks, ETFs, and FX pairs (ticker like ``USDHKD=X``).
    Every ticker the system ever uses is tracked and updated daily, so history accumulates."""
    __tablename__ = "securities"
    ticker: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    exchange: Mapped[Optional[str]] = mapped_column(String(40))
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    quote_type: Mapped[Optional[str]] = mapped_column(String(24))        # EQUITY | ETF | CURRENCY | INDEX …
    timezone: Mapped[Optional[str]] = mapped_column(String(48))
    source: Mapped[str] = mapped_column(String(24), default="yahoo")
    tracked: Mapped[bool] = mapped_column(Boolean, default=True)
    first_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    last_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    bars: Mapped[int] = mapped_column(Integer, default=0)
    backfilled_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    checks: Mapped[Optional[dict]] = mapped_column(JSON)                  # e.g. total-return check vs the source
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PriceBar(Base):
    """One trading day, **unadjusted** (as traded that day). Splits never rewrite these rows;
    split- and dividend-adjusted series are computed from them plus corporate_actions."""
    __tablename__ = "price_bars"
    ticker: Mapped[str] = mapped_column(ForeignKey("securities.ticker", ondelete="CASCADE"), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    open: Mapped[Optional[float]] = mapped_column(Float)
    high: Mapped[Optional[float]] = mapped_column(Float)
    low: Mapped[Optional[float]] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(24), default="yahoo")
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CorporateAction(Base):
    """Dividends (unadjusted cash per share, on the ex-date) and splits (ratio, e.g. 10 for 10-for-1)."""
    __tablename__ = "corporate_actions"
    ticker: Mapped[str] = mapped_column(ForeignKey("securities.ticker", ondelete="CASCADE"), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), primary_key=True)        # dividend | split
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24), default="yahoo")
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PriceRevision(Base):
    """A stored value the source later changed: the old value is kept here, never lost."""
    __tablename__ = "price_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    field: Mapped[str] = mapped_column(String(24))                         # close | open | … | dividend | split
    old: Mapped[Optional[float]] = mapped_column(Float)
    new: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24), default="yahoo")
    seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------- statement data (roadmap §3, alpha.3)
# Layer ① raw documents and layer ② filed facts. Built by libs/sv_data; stored by services/statements.py.

class RawDocumentRow(Base):
    """A fetched document, kept byte for byte (gzip file under <APP_DATA_DIR>/raw/); never modified.
    One row per distinct content: fetching unchanged content only moves ``last_checked_at``."""
    __tablename__ = "raw_documents"
    __table_args__ = (UniqueConstraint("source", "kind", "key", "sha256", name="uq_raw_documents_content"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(24))                       # sec …
    kind: Mapped[str] = mapped_column(String(32))                         # company_tickers | submissions | companyfacts
    key: Mapped[str] = mapped_column(String(64), index=True)              # CIK, or "all"
    url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)                               # relative to <APP_DATA_DIR>/raw/
    http_status: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_checked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SecTicker(Base):
    """SEC's ticker → CIK list (company_tickers.json), refreshed weekly."""
    __tablename__ = "sec_tickers"
    ticker: Mapped[str] = mapped_column(String(24), primary_key=True)
    cik: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[Optional[str]] = mapped_column(String(200))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Filer(Base):
    """A company that files with the SEC, from its submissions file."""
    __tablename__ = "filers"
    cik: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    entity_type: Mapped[Optional[str]] = mapped_column(String(40))
    sic: Mapped[Optional[str]] = mapped_column(String(8))
    sic_description: Mapped[Optional[str]] = mapped_column(String(200))
    fiscal_year_end: Mapped[Optional[str]] = mapped_column(String(4))     # MMDD
    category: Mapped[Optional[str]] = mapped_column(String(80))
    state_of_incorporation: Mapped[Optional[str]] = mapped_column(String(8))
    tickers: Mapped[Optional[list]] = mapped_column(JSON)
    exchanges: Mapped[Optional[list]] = mapped_column(JSON)
    pages_loaded: Mapped[Optional[list]] = mapped_column(JSON)            # older submissions pages already read
    facts_document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("raw_documents.id"))
    facts: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Filing(Base):
    """One filing (10-K, 10-Q, 8-K …) — what every filed fact points back to."""
    __tablename__ = "filings"
    accession: Mapped[str] = mapped_column(String(20), primary_key=True)
    cik: Mapped[str] = mapped_column(ForeignKey("filers.cik", ondelete="CASCADE"), index=True)
    form: Mapped[str] = mapped_column(String(16))
    filed: Mapped[Optional[dt.date]] = mapped_column(Date)
    report_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    accepted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    primary_document: Mapped[Optional[str]] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(String(200))
    items: Mapped[Optional[str]] = mapped_column(String(200))
    is_xbrl: Mapped[bool] = mapped_column(Boolean, default=False)
    is_inline_xbrl: Mapped[bool] = mapped_column(Boolean, default=False)
    # the filing's own XBRL instance (alpha.3 step 8): read for dimensional facts and company concepts
    instance_status: Mapped[Optional[str]] = mapped_column(String(16))     # done | none | error
    instance_document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("raw_documents.id", name="fk_filings_instance_document"))


class FiledConceptRow(Base):
    """Label and definition of a taxonomy concept (us-gaap:Revenues …), as SEC publishes them."""
    __tablename__ = "filed_concepts"
    taxonomy: Mapped[str] = mapped_column(String(24), primary_key=True)
    concept: Mapped[str] = mapped_column(String(200), primary_key=True)
    label: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)


class FiledFactRow(Base):
    """Layer ②: one reported number exactly as filed. A later filing that reports the same period
    adds another row (a restatement is never an overwrite). Values are exact decimals."""
    __tablename__ = "filed_facts"
    __table_args__ = (
        UniqueConstraint("cik", "taxonomy", "concept", "unit", "period_start", "period_end", "accession",
                         name="uq_filed_facts_fact", postgresql_nulls_not_distinct=True),
        Index("ix_filed_facts_lookup", "cik", "taxonomy", "concept"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(ForeignKey("filers.cik", ondelete="CASCADE"))
    taxonomy: Mapped[str] = mapped_column(String(24))
    concept: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(40))
    value: Mapped[Decimal] = mapped_column(Numeric)
    period_start: Mapped[Optional[dt.date]] = mapped_column(Date)          # empty for an instant
    period_end: Mapped[dt.date] = mapped_column(Date)
    fy: Mapped[Optional[int]] = mapped_column(Integer)
    fp: Mapped[Optional[str]] = mapped_column(String(4))
    form: Mapped[str] = mapped_column(String(16))
    accession: Mapped[str] = mapped_column(String(20), index=True)
    filed: Mapped[dt.date] = mapped_column(Date)
    frame: Mapped[Optional[str]] = mapped_column(String(20))
    raw_document_id: Mapped[int] = mapped_column(ForeignKey("raw_documents.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------- portfolio analysis (alpha.4)
# Private to each user (decision D10): only the owner sees their portfolios and comparisons.

class Portfolio(Base):
    """A named set of holdings and target weights, with how it is rebalanced (D8)."""
    __tablename__ = "portfolios"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    holdings: Mapped[list] = mapped_column(JSON)                  # [{"ticker": "SPY", "weight": 0.6}, …], weights sum to 1
    rebalance: Mapped[str] = mapped_column(String(12), default="none")   # none | monthly | quarterly | yearly
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Comparison(Base):
    """A saved comparison chart: up to 10 lines (tickers or portfolios), one benchmark, period and display settings."""
    __tablename__ = "comparisons"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    settings: Mapped[dict] = mapped_column(JSON)                  # the CompareIn request, as saved
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Scenario(Base):
    """A saved optimisation (roadmap §5.3 保存): the inputs, the exact data window and data fingerprint used,
    the seed, the full result as computed by the server, and the system version — enough to recompute it
    and say whether the answer is the same. Private to the owner (D10)."""
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    inputs: Mapped[dict] = mapped_column(JSON)          # the optimisation request, with currency resolved
    window_start: Mapped[dt.date] = mapped_column(Date)  # the exact data window used (a "5y" range moves; this does not)
    window_end: Mapped[dt.date] = mapped_column(Date)
    data_version: Mapped[str] = mapped_column(String(32))
    system_version: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InstanceFactRow(Base):
    """A number read from a filing's own XBRL instance that companyfacts does not carry: a dimensional fact
    (e.g. EPS per share class, revenue per segment) or a company extension concept. Plain standard-taxonomy
    facts are not stored again here — they are already in filed_facts."""
    __tablename__ = "instance_facts"
    __table_args__ = (
        UniqueConstraint("accession", "taxonomy", "concept", "unit", "period_start", "period_end", "dims_key",
                         name="uq_instance_facts_fact", postgresql_nulls_not_distinct=True),
        Index("ix_instance_facts_lookup", "cik", "taxonomy", "concept"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(ForeignKey("filers.cik", ondelete="CASCADE"))
    accession: Mapped[str] = mapped_column(String(20), index=True)
    form: Mapped[str] = mapped_column(String(16))
    filed: Mapped[dt.date] = mapped_column(Date)
    taxonomy: Mapped[str] = mapped_column(String(40))                     # us-gaap … or the filer's own prefix
    concept: Mapped[str] = mapped_column(String(200))
    extension: Mapped[bool] = mapped_column(Boolean, default=False)
    unit: Mapped[str] = mapped_column(String(40))
    value: Mapped[Decimal] = mapped_column(Numeric)
    period_start: Mapped[Optional[dt.date]] = mapped_column(Date)
    period_end: Mapped[dt.date] = mapped_column(Date)
    dimensions: Mapped[list] = mapped_column(JSON)                        # [[axis, member], …], empty when none
    dims_key: Mapped[str] = mapped_column(String(500), default="")        # canonical text of dimensions (uniqueness)
    decimals: Mapped[Optional[str]] = mapped_column(String(8))
    raw_document_id: Mapped[int] = mapped_column(ForeignKey("raw_documents.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AdjustmentPlan(Base):
    """D12: one central plan per stock (admin) and one personal plan per user per stock. Every change is a new
    version; nothing is overwritten. A personal plan starts as a copy of the central plan (or of nothing)."""
    __tablename__ = "adjustment_plans"
    __table_args__ = (UniqueConstraint("ticker", "kind", "user_id", name="uq_adjustment_plans_owner",
                                       postgresql_nulls_not_distinct=True),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(12))                 # central | personal
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))   # personal plans only
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    source_plan_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("adjustment_plans.id", ondelete="SET NULL", name="fk_adjustment_plans_source"))
    source_version: Mapped[Optional[int]] = mapped_column(Integer)   # central version copied or last merged
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AdjustmentPlanVersion(Base):
    """One saved state of a plan: the complete set of its adjustments at that time."""
    __tablename__ = "adjustment_plan_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version", name="uq_adjustment_plan_versions_version"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("adjustment_plans.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_name: Mapped[Optional[str]] = mapped_column(String(80))    # kept if the user is deleted
    note: Mapped[Optional[str]] = mapped_column(Text)
    central_version: Mapped[Optional[int]] = mapped_column(Integer)       # personal: the central version it is based on
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AdjustmentRow(Base):
    """One adjustment in one version: a standard item's value for one fiscal year or quarter."""
    __tablename__ = "adjustments"
    __table_args__ = (UniqueConstraint("version_id", "item", "period", name="uq_adjustments_item_period"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("adjustment_plan_versions.id", ondelete="CASCADE"), index=True)
    item: Mapped[str] = mapped_column(String(40))                 # standard item code, e.g. net_income
    period: Mapped[str] = mapped_column(String(16))               # FY2024 | FY2024 Q2
    original_value: Mapped[Optional[Decimal]] = mapped_column(Numeric)   # the standard value when it was adjusted
    value: Mapped[Decimal] = mapped_column(Numeric)
    reason: Mapped[str] = mapped_column(Text)
    basis: Mapped[Optional[str]] = mapped_column(Text)            # e.g. "10-K 2024 note 7" or a link
    adjusted_by: Mapped[Optional[str]] = mapped_column(String(80))       # who made this adjustment (carried across versions)
    adjusted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ------------------------------------------------------------------ document library & Q&A (D13, alpha.5)

class StockDocument(Base):
    """One document in a stock's library: SEC filing text, HKEX annual report PDF, valuation report,
    standardized statements overview, uploaded research note, or archived web source."""
    __tablename__ = "stock_documents"
    __table_args__ = (UniqueConstraint("ticker", "sha256", name="uq_stock_documents_ticker_sha"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    source_kind: Mapped[str] = mapped_column(String(20))            # sec_filing | hkex_pdf | valuation | statements | web | upload
    title: Mapped[str] = mapped_column(String(240))
    doc_date: Mapped[Optional[str]] = mapped_column(String(16))     # YYYY-MM-DD (filing date or fetch date)
    form: Mapped[Optional[str]] = mapped_column(String(20))         # 10-K | 10-Q | 8-K | PDF | ...
    accession: Mapped[Optional[str]] = mapped_column(String(24))
    url: Mapped[Optional[str]] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    summary: Mapped[Optional[str]] = mapped_column(Text)            # document-level summary
    section_summaries: Mapped[list] = mapped_column(JSON, default=list)   # [{section, page, summary}]
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ready")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class DocumentChunk(Base):
    """One section-preserving ~500-token chunk of a stock document with vector embedding for hybrid search."""
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "seq", name="uq_document_chunks_doc_seq"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("stock_documents.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(200), default="")
    page: Mapped[Optional[int]] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    url: Mapped[Optional[str]] = mapped_column(String(500))
    embedding: Mapped[Optional[list]] = mapped_column(JSON)         # L2-normalized dense vector


class QaThread(Base):
    """Per-user private Q&A conversation thread on a stock (D13)."""
    __tablename__ = "qa_threads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[Optional[str]] = mapped_column(Text)            # compressed history for long conversations
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class QaMessage(Base):
    """One turn in a private Q&A thread, storing verified clickable citations."""
    __tablename__ = "qa_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("qa_threads.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))                   # user | assistant
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)     # [{id, source_type, title, section, url, quote, ...}]
    used_web: Mapped[bool] = mapped_column(Boolean, default=False)
    used_statements: Mapped[bool] = mapped_column(Boolean, default=False)
    insufficient_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ------------------------------------------------------------------ AI suggestions (beta.2, T1/T2/T7)

class AiSuggestion(Base):
    """One verified, source-cited AI suggestion (T1 check anomaly, T2 news/catalyst, T7 thesis)
    with explicit user Accept / Dismiss workflow."""
    __tablename__ = "ai_suggestions"
    __table_args__ = (UniqueConstraint("ticker", "task_key", "source_id", name="uq_ai_suggestions_source"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    task_key: Mapped[str] = mapped_column(String(12))               # T1 | T2 | T7
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    source_id: Mapped[str] = mapped_column(String(120))
    source_title: Mapped[str] = mapped_column(String(240))
    source_url: Mapped[str] = mapped_column(String(500))
    source_date: Mapped[Optional[str]] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending | accepted | dismissed
    decided_by: Mapped[Optional[str]] = mapped_column(String(80))
    decided_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
