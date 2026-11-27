// Typed client for /api/v1. Types are generated from the backend's OpenAPI contract
// (`pnpm gen:api`), so every frontend fails to compile when the API changes under it.
// No framework and no fetch polyfill: works in browsers, React Native and (with a
// `fetch` adapter around wx.request) in WeChat mini programs.
import type { components } from "./schema";

type S = components["schemas"];
export type Health = S["Health"];
export type RunOut = S["RunOut"];
export type MethodValue = S["MethodValue"];
export type StockValuations = S["StockValuations"];
export type PriceSeries = S["PriceSeries"];
export type WatchlistEntry = S["WatchlistEntry"];
export type Plan = S["Plan"];
export type PlanTask = S["PlanTask"];
export type Job = S["Job"];
export type JobStep = S["JobStep"];
export type ValuateIn = S["ValuateIn"];
export type AiSettingsIn = S["AiSettingsIn"];
export type AiSettingsOut = S["AiSettingsOut"];
export type PriceRow = S["PriceRow"];
export type UsageOut = S["UsageOut"];
export type Mode = ValuateIn["mode"];
export type UserOut = S["UserOut"];
export type Prefs = S["Prefs"];
export type Role = UserOut["role"];
export type LoginOut = S["LoginOut"];
export type AuthStatus = S["AuthStatus"];
export type AdminUserIn = S["AdminUserIn"];
export type AdminUserUpdate = S["AdminUserUpdate"];
export type BrandingOut = S["BrandingOut"];
export type StockProfile = S["StockProfile"];
export type LimitsOut = S["LimitsOut"];
export type LimitsIn = S["LimitsIn"];
export type OpsStatus = S["OpsStatus"];
/** In the 404 body of /stocks/{ticker}/profile (detail.suggestions); not a response model, so typed here. */
export interface Suggestion { ticker: string; name?: string | null; exchange?: string | null; type?: string | null }
export type SecurityOut = S["SecurityOut"];
export type MarketStatus = S["MarketStatus"];
export type SyncResult = S["SyncResult"];
export type DataSourcesIn = S["DataSourcesIn"];
export type DataSourcesOut = S["DataSourcesOut"];
export type StatementsSyncResult = S["StatementsSyncResult"];
export type FilerOut = S["FilerOut"];
export type FiledConceptOut = S["FiledConceptOut"];
export type FiledFactOut = S["FiledFactOut"];
export type StandardStatements = S["StandardStatements"];
export type CompareIn = S["CompareIn"];
export type OptimizeIn = S["OptimizeIn"];
export type OptimizeOut = S["OptimizeOut"];
export type ScenarioSummary = S["ScenarioSummary"];
export type ScenarioOut = S["ScenarioOut"];
export type RecomputeOut = S["RecomputeOut"];
export type CompareOut = S["CompareOut"];
export type SavedComparisonOut = S["SavedComparisonOut"];
export type PortfolioIn = S["PortfolioIn"];
export type PortfolioOut = S["PortfolioOut"];
export type StatementsStatus = S["StatementsStatus"];
export type HkexPdfSyncOut = S["HkexPdfSyncOut"];
export type StatementAnalyticsOut = S["StatementAnalyticsOut"];
export type PeerFinancialCompareOut = S["PeerFinancialCompareOut"];
export type StdRow = S["StdRow"];
export type StdCell = S["StdCell"];
export type AdjustmentPlansOut = S["AdjustmentPlansOut"];
export type AdjustmentPlanOut = S["AdjustmentPlanOut"];
export type AdjustmentVersionOut = S["AdjustmentVersionOut"];
export type AdjustmentOut = S["AdjustmentOut"];
export type AdjustmentIn = S["AdjustmentIn"];
export type AdjustmentPlanIn = S["AdjustmentPlanIn"];
export type PlanChoice = "auto" | "central" | "personal" | "none";
export type StockDocumentOut = S["StockDocumentOut"];
export type StockDocumentDetail = S["StockDocumentDetail"];
export type DocSyncOut = S["DocSyncOut"];
export type QaCitationOut = S["QaCitationOut"];
export type QaMessageOut = S["QaMessageOut"];
export type QaThreadSummary = S["QaThreadSummary"];
export type QaThreadOut = S["QaThreadOut"];
export type QaAskIn = S["QaAskIn"];
export type QaAskOut = S["QaAskOut"];
export type StockMethodsOut = S["StockMethodsOut"];
export type MethodBandOut = S["MethodBandOut"];
export type MethodDetailOut = S["MethodDetailOut"];
export type ReverseDcfOut = S["ReverseDcfOut"];
export type NewsItemOut = S["NewsItemOut"];
export type AiSuggestionOut = S["AiSuggestionOut"];
export type AiSuggestionGenerateOut = S["AiSuggestionGenerateOut"];

export interface ClientConfig {
  /** "" = same origin (served behind Caddy); e.g. "https://valuation.example.com" for apps */
  baseUrl?: string;
  getToken?: () => string | null | undefined;
  getLocale?: () => string;
  fetch?: typeof fetch;
  /** "include" when the API is on another origin and signs in with a cookie; same-origin by default */
  credentials?: RequestCredentials;
  /** called on 401 (signed out or session expired) before the error is thrown */
  onUnauthorized?: () => void;
}

export class ApiError extends Error {
  /** detail: the full error body, e.g. { suggestions } for ticker_not_found */
  constructor(public status: number, public code: string, message: string, public detail?: Record<string, unknown>) {
    super(message);
  }
}

export function createClient(cfg: ClientConfig = {}) {
  const base = (cfg.baseUrl ?? "").replace(/\/$/, "") + "/api/v1";
  const doFetch = cfg.fetch ?? fetch.bind(globalThis);

  const headers = (json: boolean): Record<string, string> => {
    const h: Record<string, string> = { Accept: "application/json" };
    if (json) h["Content-Type"] = "application/json";
    const token = cfg.getToken?.();
    if (token) h.Authorization = `Bearer ${token}`;
    const lang = cfg.getLocale?.();
    if (lang) h["Accept-Language"] = lang;
    return h;
  };

  async function req<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
    const res = await doFetch(base + path, {
      method, headers: headers(body !== undefined), credentials: cfg.credentials ?? "same-origin",
      body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
    });
    if (res.status === 401 && !path.startsWith("/auth/login")) cfg.onUnauthorized?.();
    if (res.status === 204) return undefined as T;
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const d = data?.detail;
      const msg = typeof d === "string" ? d : d?.error ?? (Array.isArray(d) ? d.map((x: { msg: string }) => x.msg).join("; ") : res.statusText);
      throw new ApiError(res.status, d?.code ?? "http_" + res.status, msg, d && typeof d === "object" && !Array.isArray(d) ? d : undefined);
    }
    return data as T;
  }

  /** a POST that answers with a file (exports) */
  async function reqBlob(path: string, body: unknown): Promise<Blob> {
    const res = await doFetch(base + path, { method: "POST", headers: headers(true), credentials: cfg.credentials ?? "same-origin",
                                             body: JSON.stringify(body) });
    if (res.status === 401) cfg.onUnauthorized?.();
    if (!res.ok) {
      const d = (await res.json().catch(() => null))?.detail;
      throw new ApiError(res.status, d?.code ?? "http_" + res.status, d?.error ?? res.statusText);
    }
    return res.blob();
  }

  const q = (params: Record<string, string | number | undefined | null>) => {
    const s = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
    const out = s.toString();
    return out ? `?${out}` : "";
  };
  const enc = encodeURIComponent;

  return {
    health: () => req<Health>("GET", "/health"),

    authStatus: () => req<AuthStatus>("GET", "/auth/status"),
    login: (email: string, password: string, issueToken = false) =>
      req<LoginOut>("POST", "/auth/login", { email, password, issue_token: issueToken }),
    logout: () => req<void>("POST", "/auth/logout"),
    me: () => req<UserOut>("GET", "/auth/me"),
    updateMe: (body: { display_name?: string; prefs?: Prefs }) => req<UserOut>("PUT", "/auth/me", body),
    changePassword: (current_password: string, new_password: string) =>
      req<void>("PUT", "/auth/me/password", { current_password, new_password }),
    users: () => req<UserOut[]>("GET", "/admin/users"),
    addUser: (body: AdminUserIn) => req<UserOut>("POST", "/admin/users", body),
    updateUser: (id: string, body: AdminUserUpdate) => req<UserOut>("PUT", `/admin/users/${enc(id)}`, body),

    branding: () => req<BrandingOut>("GET", "/branding"),
    saveBrandingName: (name: string) => req<BrandingOut>("PUT", "/admin/branding", { name }),
    uploadLogo: (variant: "light" | "dark", file: Blob, filename = "logo") => {
      const fd = new FormData();
      fd.append("file", file, filename);
      return req<BrandingOut>("POST", `/admin/branding/logo/${variant}`, undefined, fd);
    },
    securities: () => req<SecurityOut[]>("GET", "/market/securities"),
    marketStatus: () => req<MarketStatus>("GET", "/market/status"),
    syncSecurity: (ticker: string, full = false) => req<SyncResult>("POST", `/admin/market/sync/${enc(ticker)}${q({ full: full ? "true" : undefined })}`),
    updateAllMarket: () => req<{ started: boolean }>("POST", "/admin/market/update-all"),
    dataSources: () => req<DataSourcesOut>("GET", "/admin/data-sources"),
    saveDataSources: (body: DataSourcesIn) => req<DataSourcesOut>("PUT", "/admin/data-sources", body),
    syncStatements: (ticker: string, force = false) =>
      req<StatementsSyncResult>("POST", `/admin/statements/sync/${enc(ticker)}${q({ force: force ? "true" : undefined })}`),
    filer: (ticker: string) => req<FilerOut>("GET", `/stocks/${enc(ticker)}/filer`),
    compare: (body: CompareIn) => req<CompareOut>("POST", "/analytics/compare", body),
    optimize: (body: OptimizeIn) => req<OptimizeOut>("POST", "/analytics/optimize", body),
    scenarios: () => req<ScenarioSummary[]>("GET", "/scenarios"),
    scenario: (id: string) => req<ScenarioOut>("GET", `/scenarios/${enc(id)}`),
    saveScenario: (name: string, inputs: OptimizeIn) => req<ScenarioOut>("POST", "/scenarios", { name, inputs }),
    deleteScenario: (id: string) => req<void>("DELETE", `/scenarios/${enc(id)}`),
    recomputeScenario: (id: string) => req<RecomputeOut>("POST", `/scenarios/${enc(id)}/recompute`),
    compareExport: (body: CompareIn, format: "xlsx" | "csv") => reqBlob(`/analytics/compare/export${q({ format })}`, body),
    comparisons: () => req<SavedComparisonOut[]>("GET", "/comparisons"),
    saveComparison: (name: string, settings: CompareIn, id?: string) =>
      req<SavedComparisonOut>(id ? "PUT" : "POST", id ? `/comparisons/${enc(id)}` : "/comparisons", { name, settings }),
    deleteComparison: (id: string) => req<void>("DELETE", `/comparisons/${enc(id)}`),
    portfolios: () => req<PortfolioOut[]>("GET", "/portfolios"),
    savePortfolio: (body: PortfolioIn, id?: string) => req<PortfolioOut>(id ? "PUT" : "POST", id ? `/portfolios/${enc(id)}` : "/portfolios", body),
    deletePortfolio: (id: string) => req<void>("DELETE", `/portfolios/${enc(id)}`),
    statementsStatus: (ticker: string) => req<StatementsStatus>("GET", `/stocks/${enc(ticker)}/statements/status`),
    fetchStatements: (ticker: string) => req<StatementsStatus>("POST", `/stocks/${enc(ticker)}/statements/fetch`),
    uploadHkexPdf: (ticker: string, file: Blob, filename = "annual_report.pdf", documentUrl?: string) => {
      const fd = new FormData();
      fd.append("file", file, filename);
      if (documentUrl) fd.append("document_url", documentUrl);
      return req<HkexPdfSyncOut>("POST", `/stocks/${enc(ticker)}/statements/hkex-pdf`, undefined, fd);
    },
    filedConcepts: (ticker: string) => req<FiledConceptOut[]>("GET", `/stocks/${enc(ticker)}/filed-concepts`),
    standardStatements: (ticker: string, view: "latest" | "original" = "latest", period: "annual" | "quarterly" = "annual", years = 10,
                         plan: PlanChoice = "auto") =>
      req<StandardStatements>("GET", `/stocks/${enc(ticker)}/statements${q({ view, period, years: period === "quarterly" ? Math.max(years, 12) : years, plan })}`),
    statementAnalytics: (ticker: string, years = 10, plan: PlanChoice = "auto") =>
      req<StatementAnalyticsOut>("GET", `/stocks/${enc(ticker)}/statements/analytics${q({ years, plan })}`),
    peerFinancialCompare: (tickers: string[]) =>
      req<PeerFinancialCompareOut>("GET", `/statements/compare${q({ tickers: tickers.join(",") })}`),
    adjustmentPlans: (ticker: string) => req<AdjustmentPlansOut>("GET", `/stocks/${enc(ticker)}/adjustment-plans`),
    adjustmentVersion: (planId: string, version: number) =>
      req<AdjustmentVersionOut>("GET", `/adjustment-plans/${enc(planId)}/versions/${version}`),
    saveAdjustmentPlan: (ticker: string, kind: "central" | "personal", body: AdjustmentPlanIn) =>
      req<AdjustmentPlansOut>("PUT", `/stocks/${enc(ticker)}/adjustment-plans/${kind}`, body),
    filedFacts: (ticker: string, concept: string, taxonomy = "us-gaap") =>
      req<FiledFactOut[]>("GET", `/stocks/${enc(ticker)}/filed-facts${q({ concept, taxonomy })}`),
    removeLogo: (variant: "light" | "dark") => req<BrandingOut>("DELETE", `/admin/branding/logo/${variant}`),
    /** absolute URL for an <img> (branding URLs from the API are root-relative) */
    assetUrl: (path: string) => (cfg.baseUrl ?? "").replace(/\/$/, "") + path,
    lookup: (ticker: string) => req<Record<string, unknown>>("GET", `/stocks/${enc(ticker)}/lookup`),
    profile: (ticker: string) => req<StockProfile>("GET", `/stocks/${enc(ticker)}/profile`),
    limits: () => req<LimitsOut>("GET", "/admin/limits"),
    saveLimits: (body: LimitsIn) => req<LimitsOut>("PUT", "/admin/limits", body),
    ops: () => req<OpsStatus>("GET", "/admin/ops"),
    valuations: (ticker: string) => req<StockValuations>("GET", `/stocks/${enc(ticker)}/valuations`),
    methods: (ticker: string) => req<StockMethodsOut>("GET", `/stocks/${enc(ticker)}/methods`),
    prices: (ticker: string, days = 365) => req<PriceSeries>("GET", `/stocks/${enc(ticker)}/prices${q({ days })}`),
    excelUrl: (runId: string, lang?: string) => `${base}/runs/${enc(runId)}/excel${q({ lang })}`,
    reportUrl: (runId: string, lang?: string) => `${base}/runs/${enc(runId)}/report${q({ lang })}`,
    statementsExportUrl: (ticker: string, format: "xlsx" | "csv", view: "latest" | "original", period: "annual" | "quarterly",
                          plan: PlanChoice = "auto") =>
      `${base}/stocks/${enc(ticker)}/statements/export${q({ format, view, period, years: period === "quarterly" ? 12 : 10, plan })}`,

    watchlist: () => req<WatchlistEntry[]>("GET", "/watchlist"),
    addToWatchlist: (ticker: string) => req<WatchlistEntry>("POST", "/watchlist", { ticker }),
    removeFromWatchlist: (ticker: string) => req<void>("DELETE", `/watchlist/${enc(ticker)}`),

    plan: (ticker: string, mode: Mode, tasks?: string[]) =>
      req<Plan>("GET", `/ai/plan${q({ ticker, mode, tasks: tasks?.join(",") })}`),
    startValuation: (body: ValuateIn) => req<Job>("POST", "/valuations", body),
    job: (id: string) => req<Job>("GET", `/jobs/${enc(id)}`),
    activeJobs: () => req<Job[]>("GET", "/jobs"),
    cancelJob: (id: string) => req<Job>("POST", `/jobs/${enc(id)}/cancel`),
    /** SSE URL; EventSource cannot send headers, so the token goes in the query. */
    jobEventsUrl: (id: string) => `${base}/jobs/${enc(id)}/events${q({ access_token: cfg.getToken?.() })}`,

    aiSettings: () => req<AiSettingsOut>("GET", "/settings/ai"),
    saveAiSettings: (body: Partial<AiSettingsIn>) => req<AiSettingsOut>("PUT", "/settings/ai", body),
    testAi: () => req<{ ok: boolean; error?: string | null; seconds: number; tokens_in?: number | null;
                        tokens_out?: number | null; cost_usd?: number | null; priced: boolean }>("POST", "/settings/ai/test"),
    priceTable: () => req<PriceRow[]>("GET", "/settings/prices"),
    savePrice: (row: PriceRow) => req<PriceRow>("PUT", "/settings/prices", row),
    usage: (month?: string) => req<UsageOut>("GET", `/ai/usage${q({ month })}`),

    documents: (ticker: string) => req<StockDocumentOut[]>("GET", `/stocks/${enc(ticker)}/documents`),
    syncDocuments: (ticker: string, includeSec = true, maxFilings = 3) =>
      req<DocSyncOut>("POST", `/stocks/${enc(ticker)}/documents/sync${q({ include_sec: includeSec ? "true" : "false", max_filings: maxFilings })}`),
    uploadDocument: (ticker: string, file: Blob, filename: string, title?: string) => {
      const fd = new FormData();
      fd.append("file", file, filename);
      if (title) fd.append("title", title);
      return req<StockDocumentOut>("POST", `/stocks/${enc(ticker)}/documents/upload`, undefined, fd);
    },
    documentDetail: (ticker: string, docId: number) => req<StockDocumentDetail>("GET", `/stocks/${enc(ticker)}/documents/${docId}`),
    deleteDocument: (ticker: string, docId: number) => req<void>("DELETE", `/stocks/${enc(ticker)}/documents/${docId}`),
    qaThreads: (ticker: string) => req<QaThreadSummary[]>("GET", `/stocks/${enc(ticker)}/qa/threads`),
    qaThread: (ticker: string, threadId: string) => req<QaThreadOut>("GET", `/stocks/${enc(ticker)}/qa/threads/${enc(threadId)}`),
    deleteQaThread: (ticker: string, threadId: string) => req<void>("DELETE", `/stocks/${enc(ticker)}/qa/threads/${enc(threadId)}`),
    qaAsk: (ticker: string, body: QaAskIn) => req<QaAskOut>("POST", `/stocks/${enc(ticker)}/qa/ask`, body),

    news: (ticker: string, limit = 25) => req<NewsItemOut[]>("GET", `/stocks/${enc(ticker)}/news${q({ limit })}`),
    suggestions: (ticker: string) => req<AiSuggestionOut[]>("GET", `/stocks/${enc(ticker)}/suggestions`),
    generateSuggestions: (ticker: string, tasks: ("T1" | "T2")[] = ["T1", "T2"]) =>
      req<AiSuggestionGenerateOut>("POST", `/stocks/${enc(ticker)}/suggestions/generate`, { tasks }),
    decideSuggestion: (id: number, status: "pending" | "accepted" | "dismissed") =>
      req<AiSuggestionOut>("PUT", `/suggestions/${id}`, { status }),
  };
}
export type Api = ReturnType<typeof createClient>;

/**
 * Follow a job until it finishes: SSE where available, polling otherwise
 * (mini programs and some proxies). Returns a function that stops following.
 */
export function followJob(api: Api, id: string, onJob: (job: Job) => void,
                          opts: { pollMs?: number; useSse?: boolean } = {}): () => void {
  let stopped = false;
  const terminal = (j: Job) => ["done", "failed", "cancelled"].includes(j.status);
  const poll = async () => {
    while (!stopped) {
      try {
        const j = await api.job(id);
        onJob(j);
        if (terminal(j)) return;
      } catch { /* transient; keep polling */ }
      await new Promise((r) => setTimeout(r, opts.pollMs ?? 1000));
    }
  };
  if (opts.useSse !== false && typeof EventSource !== "undefined") {
    const es = new EventSource(api.jobEventsUrl(id));
    es.addEventListener("job", (ev) => {
      const j = JSON.parse((ev as MessageEvent).data) as Job;
      onJob(j);
      if (terminal(j)) es.close();
    });
    es.onerror = () => { es.close(); if (!stopped) void poll(); };
    return () => { stopped = true; es.close(); };
  }
  void poll();
  return () => { stopped = true; };
}
