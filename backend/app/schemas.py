"""API request and response models. The OpenAPI contract for every frontend is generated from these."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Mode = Literal["none", "lite", "standard", "deep"]


class Health(BaseModel):
    ok: bool
    version: str
    api: str = "v1"
    database: str
    queue: str
    ai_configured: bool
    disclaimer: str = "本系統產出不構成投資建議。"


# ---------------------------------------------------------------- valuations

class MethodValue(BaseModel):
    method: str
    scenario: str
    value: Optional[float]
    weight: Optional[float] = None


class RunOut(BaseModel):
    id: str
    ticker: str
    company: Optional[str]
    run_at: str
    valuation_date: str
    status: str
    trigger: str
    ai_mode: str
    source: str
    error: Optional[str] = None
    price: Optional[float]
    price_date: Optional[str]
    trading_ccy: Optional[str]
    composite_value: Optional[float]
    weights: Optional[dict] = None
    wacc: Optional[float]
    terminal_g: Optional[float]
    verify_pass: Optional[bool]
    assumption_source: Optional[str]
    ai_cost_usd: float
    ai_tokens_in: int
    ai_tokens_out: int
    has_excel: bool
    methods: list[MethodValue]
    data_gaps: list[str] = []


class StockValuations(BaseModel):
    ticker: str
    company: Optional[str]
    currency: Optional[str]
    runs: list[RunOut]
    latest: Optional[RunOut]
    previous: Optional[RunOut]


class PricePoint(BaseModel):
    date: str
    close: float = Field(description="未調整收市價（當日成交價）")
    adj_close: Optional[float] = Field(None, description="按拆股與派息調整的總回報價格（最新一日等於收市價）")


class Coverage(BaseModel):
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    bars: int = 0
    updated_at: Optional[str] = None


class PriceSeries(BaseModel):
    ticker: str
    currency: Optional[str]
    points: list[PricePoint]
    source: str = "Yahoo Finance（系統每日累積）"
    coverage: Optional[Coverage] = None
    error: Optional[str] = None


# ---------------------------------------------------------------- watchlist

class WatchlistIn(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=24)


class WatchlistEntry(BaseModel):
    ticker: str
    company: Optional[str]
    added_at: str
    latest: Optional[RunOut]
    status: Literal["ok", "never_valued", "below_bear", "above_bull"]


# ---------------------------------------------------------------- AI plan / jobs

class PlanTask(BaseModel):
    key: str
    label: str
    detail: str
    in_mode: bool
    selected: bool
    available: bool
    will_run: bool
    skip_reason: Optional[str]
    skip_code: Optional[str] = Field(None, description="mode_none | no_ai_key | next_phase | name_spec_locked | not_selected | over_budget")
    est_tokens_in: int
    est_tokens_out: int
    est_cost_usd: Optional[float]


class PlanTotal(BaseModel):
    tokens_in: int
    tokens_out: int
    cost_usd: float
    cost_low_usd: float
    cost_high_usd: float


class Plan(BaseModel):
    ticker: str
    mode: Mode
    mode_label: str
    model: Optional[str]
    priced: bool
    calibration: float
    tasks: list[PlanTask]
    total: PlanTotal
    budget_per_run_usd: float
    budget_monthly_usd: float
    month_spent_usd: float
    over_budget: bool
    over_budget_action: Literal["fallback", "ask"]
    has_name_spec: bool


class ValuateIn(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=24)
    mode: Mode = "standard"
    tasks: Optional[list[str]] = Field(None, description="勾選的 AI 任務（例如 [\"T3\"]）；省略＝該模式的預設")
    valuation_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    budget_usd: Optional[float] = Field(None, ge=0, description="本次上限；省略＝設定頁的每次上限")


class JobStep(BaseModel):
    key: str
    label: str
    kind: Literal["data", "ai", "engine"]
    status: Literal["pending", "active", "done", "cached", "skipped", "failed", "cancelled"]
    note: Optional[str] = None
    note_code: Optional[str] = Field(None, description="與 PlanTask.skip_code 相同的代碼，或 ai_failed / ai_rejected / usage_unreported / cancelled")
    est_cost_usd: Optional[float] = None
    est_tokens_in: Optional[int] = None
    est_tokens_out: Optional[int] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None


class Job(BaseModel):
    id: str
    ticker: str
    mode: Mode
    valuation_date: str
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    progress: int
    activity: Optional[str]
    steps: list[JobStep]
    estimate: dict
    budget_usd: Optional[float]
    cost_usd: float
    tokens_in: int
    tokens_out: int
    run_id: Optional[str]
    result: Optional[dict]
    error: Optional[str]
    note: Optional[str]
    version: int


# ---------------------------------------------------------------- settings / usage

class AiSettingsIn(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = Field(None, description="只寫不讀；空字串＝不變更")
    clear_api_key: bool = False
    budget_per_run_usd: Optional[float] = Field(None, ge=0)
    budget_monthly_usd: Optional[float] = Field(None, ge=0)
    over_budget: Optional[Literal["fallback", "ask"]] = None
    notify_at_pct: Optional[int] = Field(None, ge=1, le=100)


class KeyMask(BaseModel):
    set: bool
    length: int
    tail: Optional[str]


class AiSettingsOut(BaseModel):
    provider: str
    base_url: str
    model: str
    api_key: KeyMask
    configured: bool
    local: bool
    budget_per_run_usd: float
    budget_monthly_usd: float
    over_budget: Literal["fallback", "ask"]
    notify_at_pct: int
    presets: list[dict]


class PriceRow(BaseModel):
    model: str = Field(..., min_length=1, max_length=80)
    input: float = Field(..., ge=0)
    cached_input: Optional[float] = Field(None, ge=0)
    output: float = Field(..., ge=0)
    note: Optional[str] = None
    checked_on: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    source_url: Optional[str] = None
    stale: bool = False


class UsageOut(BaseModel):
    month: str
    total_usd: float
    budget_monthly_usd: float
    runs: int
    runs_with_ai: int
    tokens_in: int
    tokens_out: int
    calibration: float
    daily: list[dict]
    by_task: list[dict]
    by_ticker: list[dict]


# ---------------------------------------------------------------- accounts (D6)

Role = Literal["admin", "member"]
Rebalance = Literal["none", "monthly", "quarterly", "annual"]


class Prefs(BaseModel):
    """Per-user preferences. Display currency for portfolios and comparisons (D7); rebalancing (D8)."""
    display_currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    rebalance: Rebalance = "none"
    locale: Optional[Literal["zh-Hant", "zh-Hans", "en"]] = None
    theme: Optional[Literal["light", "dark", "system"]] = None


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: Role
    is_active: bool
    prefs: Prefs
    created_at: str
    last_login_at: Optional[str] = None


class AuthStatus(BaseModel):
    needs_setup: bool = Field(description="尚未有任何帳號：需要先用指令建立第一個管理員")


class LoginIn(BaseModel):
    email: str
    password: str
    issue_token: bool = Field(False, description="App／小程序用：回應中附上存取令牌（網頁用 Cookie，不需要）")


class LoginOut(BaseModel):
    user: UserOut
    token: Optional[str] = None


class MeIn(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=80)
    prefs: Optional[Prefs] = None


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


class AdminUserIn(BaseModel):
    email: str
    display_name: str = ""
    role: Role = "member"
    password: str


class AdminUserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=80)
    role: Optional[Role] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, description="重設密碼；該用戶所有已登入的裝置會被登出")


# ---------------------------------------------------------------- branding (5.2)

class BrandingOut(BaseModel):
    name: str
    logo_light_url: Optional[str] = None
    logo_dark_url: Optional[str] = None
    has_custom_icon: bool = False
    updated_at: Optional[str] = None


class BrandingIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)


# ---------------------------------------------------------------- market data

class SecurityOut(BaseModel):
    ticker: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    quote_type: Optional[str] = None
    tracked: bool
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    bars: int = 0
    backfilled_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_error: Optional[str] = None
    checks: Optional[dict] = None


class MarketStatus(BaseModel):
    scheduler: Literal["worker", "inline", "off"]
    timezone: str
    times: list[str]
    next_runs: list[str]
    last_run: Optional[dict] = None
    securities: int
    bars: int


class SyncResult(BaseModel):
    ticker: str
    mode: str
    bars_added: int
    bars_revised: int
    actions_added: int
    actions_revised: int
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    bars: int


class RevisionOut(BaseModel):
    ticker: str
    date: str
    field: str
    old: Optional[float] = None
    new: Optional[float] = None
    source: str
    seen_at: str
