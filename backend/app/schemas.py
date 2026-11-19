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
    statement_source: Optional[str] = Field(None, description="財報來源：sec_standard（SEC 標準層）｜yahoo｜builtin（內建規格）")
    ruleset_version: Optional[str] = Field(None, description="SEC 標準層的規則版本（含公司覆蓋）")
    adjustment_plan: Optional[dict] = Field(None, description="套用的調整方案 {kind, id, version, label}；空值＝系統預設")


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


class Suggestion(BaseModel):
    ticker: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    type: Optional[str] = None


class StockProfile(BaseModel):
    """What the user checks before valuing: the right company, exchange and currency."""
    ticker: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    financial_currency: Optional[str] = Field(None, description="財報貨幣（估值模型的貨幣）")
    quote_type: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[float] = None
    price: Optional[float] = None
    price_date: Optional[str] = None
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None
    website: Optional[str] = None
    summary: Optional[str] = None
    source: str = "Yahoo Finance"
    has_name_spec: bool = False
    valued: bool = False
    stale: bool = Field(False, description="數據源暫時無法連線，這是之前保存的資料")
    as_of: Optional[float] = Field(None, description="stale 時：資料保存的時間（Unix 秒）")


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
    force: bool = Field(False, description="同一代號、日期、AI 模式、調整方案版本近期已估值時仍要重算")
    plan: Literal["auto", "central", "personal", "none"] = Field(
        "auto", description="美股財報套用的調整方案（D12）：auto＝我的個人方案 → 中央方案 → 不調整")


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


class PlanRef(BaseModel):
    kind: Literal["central", "personal"]
    id: str
    version: int
    label: str


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
    joined: bool = Field(False, description="同一代號已在估值：此回應是加入那個工作，而非新開一個")
    queue_position: Optional[int] = Field(None, description="排隊中時，前面還有幾個工作＋1")
    requested_by: Optional[str] = None
    plan: Optional[PlanRef] = Field(None, description="本次估值套用的調整方案與版本（D12）；空值＝系統預設或財報不是取自 SEC 標準層")


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


# ---------------------------------------------------------------- limits and operations (P0)

class LimitsIn(BaseModel):
    lookup_per_minute: Optional[int] = Field(None, ge=1)
    prices_per_minute: Optional[int] = Field(None, ge=1)
    valuations_per_hour: Optional[int] = Field(None, ge=1)
    concurrent_valuations_per_user: Optional[int] = Field(None, ge=1)
    queue_max: Optional[int] = Field(None, ge=1)
    revalue_cooldown_hours: Optional[int] = Field(None, ge=0)


class LimitsOut(BaseModel):
    lookup_per_minute: int
    prices_per_minute: int
    valuations_per_hour: int
    concurrent_valuations_per_user: int
    queue_max: int
    revalue_cooldown_hours: int
    defaults: dict


class OpsStatus(BaseModel):
    providers: dict
    queue: dict


# ---------------------------------------------------------------- statement data (alpha.3)

class DataSourcesIn(BaseModel):
    sec_organisation: Optional[str] = Field(None, description="SEC 要求的機構名稱，例如 \"Example Research\"；與電郵同時空白＝清除")
    sec_email: Optional[str] = Field(None, description="SEC 要求的聯絡電郵")


class DataSourcesOut(BaseModel):
    sec_organisation: Optional[str] = None
    sec_email: Optional[str] = None
    sec_contact: Optional[str] = Field(None, description="實際送給 SEC 的 User-Agent（機構名稱＋電郵）")
    sec_ready: bool
    statements_last_run: Optional[dict] = Field(None, description="上次每日檢查新申報的結果（只在 GET 回傳）")


class StatementsSyncResult(BaseModel):
    ticker: str
    cik: str
    name: Optional[str] = None
    filings_added: int
    facts_added: int
    facts_total: int
    has_xbrl: bool
    unchanged: bool
    duplicates: int = 0
    conflicts: int = 0
    skipped: int = 0
    instances_read: int = Field(0, description="本次讀取的年報 XBRL instance 數目（每份申報只讀一次）")
    instance_facts: int = Field(0, description="新增的維度數字與公司自訂科目數字")
    instances_missing: int = Field(0, description="找不到 instance 的年報（不再重試）")


class FilerOut(BaseModel):
    cik: str
    name: Optional[str] = None
    entity_type: Optional[str] = None
    sic: Optional[str] = None
    sic_description: Optional[str] = None
    fiscal_year_end: Optional[str] = None
    category: Optional[str] = None
    tickers: list[str] = []
    exchanges: list[str] = []
    filings: int
    facts: int
    synced_at: Optional[str] = None
    last_error: Optional[str] = None


class FiledConceptOut(BaseModel):
    taxonomy: str
    concept: str
    source: Literal["companyfacts", "instance"] = Field("companyfacts", description="instance：公司自訂科目，來自年報 XBRL instance")
    label: Optional[str] = None
    units: list[str]
    facts: int
    first_end: str
    last_end: str


class FiledFactOut(BaseModel):
    taxonomy: str
    concept: str
    source: Literal["companyfacts", "instance"] = "companyfacts"
    unit: str
    value: str = Field(description="申報原值，精確十進位字串（不經浮點數）")
    period_start: Optional[str] = None
    period_end: str
    fy: Optional[int] = None
    fp: Optional[str] = None
    form: str
    accession: str
    filed: str
    frame: Optional[str] = None
    index_url: str
    document_url: Optional[str] = None


class StatementsStatus(BaseModel):
    ticker: str
    sec_ready: bool = Field(description="管理員已設定 SEC 聯絡資料")
    us_filer: Optional[bool] = Field(None, description="是否在 SEC 代號清單中；清單未載入時為 null")
    synced: bool
    synced_at: Optional[str] = None
    pending: bool = Field(description="正在背景取得")
    facts: int = 0
    last_error: Optional[str] = None


class StdYear(BaseModel):
    label: str = Field(description="FY2025｜FY2025 Q1｜TTM")
    start: str
    end: str
    kind: Literal["annual", "quarter", "ttm"] = "annual"
    fiscal_year: Optional[str] = None
    quarter: Optional[int] = None


class StdPart(BaseModel):
    sign: int = Field(description="+1 或 −1：推算時加或減")
    value: str
    form: str
    accession: str
    filed: str
    period_start: Optional[str] = None
    period_end: str
    index_url: Optional[str] = None
    document_url: Optional[str] = None


class StdCell(BaseModel):
    value: str = Field(description="標準值，精確十進位字串")
    method: Literal["concept", "formula", "derived", "ttm", "adjusted"] = Field(
        description="concept 申報科目｜formula 公式｜derived 由年初至今數字推算的季度｜ttm 近十二個月｜adjusted 未申報、由調整方案提供")
    rule_id: str
    concept: Optional[str] = Field(None, description="取自的申報科目（taxonomy:Concept）")
    restated: bool = False
    change: Optional[Literal["restated", "split_adjusted"]] = Field(None, description="同一期間在較新申報中數字不同的原因")
    original_value: Optional[str] = None
    latest_value: Optional[str] = None
    formula: Optional[str] = Field(None, description="公式（標準科目代碼，以 + − 連接；? 表示可缺、按零計）")
    raw_value: Optional[str] = Field(None, description="申報原值（股份分拆調整前）")
    split_factor: str = Field("1", description="申報後發生、經申報文件確認的股份分拆累計比例；每股數字已除以、股數已乘以此數")
    inputs: list[str] = Field(default_factory=list, description="公式用到的標準科目")
    assumed_zero: list[str] = Field(default_factory=list, description="公式中未申報、按零計算的可選項目")
    adjusted_from: Optional[str] = Field(None, description="調整方案（D12）：調整前的標準值")
    adjustment: Optional[dict] = Field(None, description="直接調整：reason、basis、by、at；由其他調整推出：from（項目或季度）")
    form: Optional[str] = None
    accession: Optional[str] = None
    filed: Optional[str] = None
    index_url: Optional[str] = None
    document_url: Optional[str] = None
    parts: list[StdPart] = Field(default_factory=list, description="推算季度或 TTM 用到的申報數字")


class StdRow(BaseModel):
    code: str
    statement: Literal["income", "balance", "cash_flow"]
    unit: Literal["money", "per_share", "shares"]
    total: bool = Field(False, description="小計或合計（粗體顯示）")
    cells: list[Optional[StdCell]] = Field(description="與 years 一一對應；沒有數字時為 null；行的次序即顯示次序")


class CheckFailure(BaseModel):
    rule_id: str
    period: str
    severity: Literal["error", "warning", "info"]
    values: dict = Field(default_factory=dict, description="比較的數字（精確字串）")
    difference: Optional[str] = Field(None, description="相對差異（0.0074 = 0.74%）")


class StatementChecks(BaseModel):
    checkset: str
    version: str
    yahoo_fetched_at: Optional[str] = Field(None, description="交叉核對用的 Yahoo 數字取得時間；沒有則不做跨來源檢查")
    grades: dict[str, Literal["A", "B", "C"]] = Field(default_factory=dict, description="各期數據品質：A 全部通過、B 有警告、C 有錯誤")
    summary: dict[str, dict[str, int]] = Field(default_factory=dict, description="各期 pass／fail／skip 數目")
    failures: list[CheckFailure] = Field(default_factory=list)


class RatioCell(BaseModel):
    value: str = Field(description="比率（0.2142 = 21.42%；倍數與日數照原值）")
    inputs: dict = Field(default_factory=dict, description="計算用到的標準科目數值；平均值附期末與一年前數字")


class RatioRow(BaseModel):
    code: str
    kind: Literal["pct", "times", "days"]
    cells: list[Optional[RatioCell]]


class GrowthRow(BaseModel):
    code: str = Field(description="標準科目")
    cells: list[Optional[str]] = Field(description="按年增長（季度與去年同季比較）；上期為零或負數時為 null")


class CagrValue(BaseModel):
    code: str
    years: int
    value: str


class StatementRatios(BaseModel):
    ratioset: str
    version: str
    rows: list[RatioRow]
    growth: list[GrowthRow]
    cagr: list[CagrValue] = Field(default_factory=list, description="至最近財年的 3 年與 5 年複合增長率")


class ShareClassCell(BaseModel):
    value: str = Field(description="與每股盈利列相同的股份基礎（已按確認的分拆調整）")
    raw_value: str = Field(description="申報原值")
    split_factor: str
    form: str
    accession: str
    filed: str
    index_url: str
    document_url: Optional[str] = None


class ShareClassRow(BaseModel):
    member: str = Field(description="股份類別成員，例如 brka:ClassAMember（XBRL instance 的 StatementClassOfStockAxis）")
    label: str
    item: Literal["eps_basic", "eps_diluted"]
    cells: list[Optional[ShareClassCell]]


class StandardStatements(BaseModel):
    ticker: str
    cik: str
    name: Optional[str] = None
    template: Literal["general", "bank", "insurance"] = Field(description="按 SIC 判斷的行業模板")
    template_applied: str = Field(description="實際套用的科目表（general｜bank｜insurance）")
    checks: StatementChecks
    ratios: StatementRatios
    ruleset: str
    ruleset_version: str
    currency: Optional[str] = None
    view: Literal["latest", "original"]
    period: Literal["annual", "quarterly"] = "annual"
    as_of: Optional[str] = None
    splits_applied: list[dict] = Field(default_factory=list, description="已套用的股份分拆（日期、比例），只計申報文件確認過的")
    years: list[StdYear]
    rows: list[StdRow]
    share_classes: list[ShareClassRow] = Field(default_factory=list,
                                               description="按股份類別的每股盈利（只限年度，來自年報 XBRL instance）")
    plan: Optional[PlanRef] = Field(None, description="套用的調整方案（D12）；空值＝系統預設（不調整）")
    adjustments_not_shown: list[str] = Field(default_factory=list, description="方案中不在所顯示期間的調整（項目 期間）")


# ---------------------------------------------------------------- adjustment plans (D12, alpha.3)

class AdjustmentIn(BaseModel):
    item: str = Field(max_length=40, description="標準科目代碼，例如 net_income")
    period: str = Field(max_length=16, description="FY2024 或 FY2024 Q2（TTM 不可直接調整，由季度推出）")
    value: str = Field(max_length=40, description="調整後的數值（精確十進位字串，原始單位，不是百萬）")
    reason: str = Field(min_length=1, max_length=2000)
    basis: Optional[str] = Field(None, max_length=2000, description="依據，例如「2024 年報附註 7」或連結")


class AdjustmentPlanIn(BaseModel):
    adjustments: list[AdjustmentIn] = Field(default_factory=list, max_length=500, description="新版本的全部調整（完整一套，不是增量）")
    note: Optional[str] = Field(None, max_length=2000, description="本版本的說明")
    base_version: int = Field(ge=0, description="編輯時所依據的版本（新方案為 0）；方案已被更新時回 409")
    central_version: Optional[int] = Field(None, ge=0, description="個人方案：本版本已合併至中央方案的哪一版")


class AdjustmentOut(BaseModel):
    item: str
    period: str
    value: str
    original_value: Optional[str] = Field(None, description="調整時的標準值（未申報時為空）")
    reason: str
    basis: Optional[str] = None
    adjusted_by: Optional[str] = None
    adjusted_at: str


class AdjustmentVersionOut(BaseModel):
    plan_id: str
    kind: Literal["central", "personal"]
    version: int
    created_by_name: Optional[str] = None
    created_at: Optional[str] = None
    note: Optional[str] = None
    central_version: Optional[int] = None
    adjustments: list[AdjustmentOut]


class AdjustmentVersionSummary(BaseModel):
    version: int
    created_by_name: Optional[str] = None
    created_at: str
    note: Optional[str] = None
    central_version: Optional[int] = None
    adjustments: int


class AdjustmentPlanOut(BaseModel):
    id: str
    kind: Literal["central", "personal"]
    current_version: int
    source_version: Optional[int] = Field(None, description="個人方案：複製或最後合併的中央方案版本")
    versions: list[AdjustmentVersionSummary] = Field(description="全部版本，最新在前")
    current: AdjustmentVersionOut


class AdjustmentPlansOut(BaseModel):
    ticker: str
    central: Optional[AdjustmentPlanOut] = None
    personal: Optional[AdjustmentPlanOut] = Field(None, description="只有自己的個人方案")
    central_updated: bool = Field(description="中央方案在我的個人方案複製或最後合併之後有新版本")


# ---------------------------------------------------------------- portfolio analysis (alpha.4)

class CompareLine(BaseModel):
    kind: Literal["ticker", "portfolio"] = "ticker"
    ref: str = Field(max_length=40, description="代號（ticker）或已保存組合的編號（portfolio）")


class CompareIn(BaseModel):
    lines: list[CompareLine] = Field(min_length=1, max_length=10)
    benchmark: int = Field(0, ge=0, le=9, description="lines 中作為 benchmark 的位置")
    range: Literal["1y", "2y", "3y", "5y", "10y", "ytd", "max", "custom"] = "3y"
    start: Optional[str] = Field(None, description="自訂起日（range=custom）")
    end: Optional[str] = Field(None, description="自訂止日；預設為最新共同交易日")
    currency: Optional[str] = Field(None, max_length=8, description="顯示貨幣；預設為用戶偏好（D7）")
    rf: float = Field(0.0, ge=-0.05, le=0.5, description="無風險利率（年，0.04 = 4%）")


class CompareLineOut(BaseModel):
    key: str
    kind: str
    ref: str
    label: str
    currency: str = Field(description="原交易貨幣（已換算為顯示貨幣）")
    first_date: Optional[str] = None
    values: list[float] = Field(description="以起點為 100 的總回報指數（股息再投資），與 dates 對應")


class RelativeOut(BaseModel):
    key: str
    values: list[float] = Field(description="相對強弱：標的 ÷ benchmark，起點為 100")


class CompareOut(BaseModel):
    currency: str
    rf: float
    frequency: Literal["daily", "weekly"] = Field(description="計算回報指標用的頻率；跨交易所時改用每週回報（收市時間不同）")
    benchmark: str
    window: dict
    dates: list[str]
    lines: list[CompareLineOut]
    relative: list[RelativeOut]
    metrics: list[dict] = Field(description="每條線的指標；相對 benchmark 的指標只限非 benchmark 的線")
    periods: list[dict] = Field(description="每條線的期間回報（1m、3m、6m、ytd、1y、3y、5y；3y 與 5y 為年化）")


class SavedComparisonIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    settings: CompareIn


class SavedComparisonOut(BaseModel):
    id: str
    name: str
    settings: CompareIn
    updated_at: str


class Holding(BaseModel):
    ticker: str = Field(max_length=24)
    weight: float = Field(gt=0, description="權重；可用百分比或小數，系統會換算成合計 100%")


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    holdings: list[Holding] = Field(min_length=1, max_length=10)
    rebalance: Literal["none", "monthly", "quarterly", "yearly"] = "none"
    notes: Optional[str] = Field(None, max_length=2000)


class PortfolioOut(BaseModel):
    id: str
    name: str
    holdings: list[Holding]
    rebalance: str
    notes: Optional[str] = None
    updated_at: str


class OptimizeAsset(BaseModel):
    ticker: str = Field(max_length=24)
    min: int = Field(0, ge=0, le=100, description="最低權重（%）")
    max: int = Field(100, ge=0, le=100, description="最高權重（%）")


class OptimizeIn(BaseModel):
    assets: list[OptimizeAsset] = Field(min_length=2, max_length=10)
    objective: dict[Literal["sharpe", "max_drawdown", "return"], float] = Field(
        default_factory=lambda: {"sharpe": 1.0}, description="得分條件與權重；單一條件直接最大化，多個條件先標準化再加權")
    range: Literal["1y", "2y", "3y", "5y", "10y", "ytd", "max", "custom"] = "5y"
    start: Optional[str] = None
    end: Optional[str] = None
    currency: Optional[str] = Field(None, max_length=8, description="顯示貨幣；預設為用戶偏好（D7）")
    rebalance: Literal["none", "monthly", "quarterly", "yearly"] = Field("none", description="再平衡（D8）；預設不再平衡")
    rf: float = Field(0.0, ge=-0.05, le=0.5)
    seed: int = Field(42, ge=0, le=2**31 - 1, description="隨機種子；相同輸入與種子，結果相同")
    samples: int = Field(20000, ge=500, le=50000)
    oos_fraction: float = Field(0.0, ge=0.0, le=0.5, description="樣本外檢驗：最後這部分期間只用作驗證（0 = 不做）")


class OptimizeOut(BaseModel):
    weights: dict[str, int] = Field(description="最佳權重（%，合計 100）")
    metrics: dict[str, Optional[float]] = Field(description="最佳化期間的指標（return 為年化回報）")
    full_period: dict[str, Optional[float]]
    score: float
    breakdown: dict[str, dict]
    top: list[dict]
    frontier: list[dict]
    cloud: list[dict] = Field(description="抽樣組合的波動率與回報（作圖用，最多 1,500 個）")
    assets: list[dict]
    out_of_sample: Optional[dict] = None
    samples: int
    seed: int
    refined: bool = Field(description="SLSQP 精修是否找到比抽樣更好的組合")
    frequency: Literal["daily", "weekly"]
    currency: str
    rebalance: str
    rf: float
    objective: dict
    window: dict
    data_version: str = Field(description="所用數據的指紋；相同指紋、相同輸入，結果必定相同")
    system_version: str
    bounds: list[dict]


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    inputs: OptimizeIn


class ScenarioSummary(BaseModel):
    id: str
    name: str
    created_at: str
    window_start: str
    window_end: str
    data_version: str
    system_version: str
    weights: dict[str, int]
    objective: dict


class ScenarioOut(ScenarioSummary):
    inputs: dict
    result: OptimizeOut


class RecomputeOut(BaseModel):
    identical: bool = Field(description="權重與得分完全相同")
    same_data: bool = Field(description="數據指紋相同（期間內沒有任何價格被修訂）")
    same_system: bool
    original: dict
    recomputed: dict
    weight_changes: dict[str, list[int]] = Field(default_factory=dict, description="權重有變的資產：[原本, 重算]")
