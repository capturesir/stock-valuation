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

export interface ClientConfig {
  /** "" = same origin (served behind Caddy); e.g. "https://valuation.example.com" for apps */
  baseUrl?: string;
  getToken?: () => string | null | undefined;
  getLocale?: () => string;
  fetch?: typeof fetch;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
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

  async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await doFetch(base + path, {
      method, headers: headers(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 204) return undefined as T;
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const d = data?.detail;
      const msg = typeof d === "string" ? d : d?.error ?? (Array.isArray(d) ? d.map((x: { msg: string }) => x.msg).join("; ") : res.statusText);
      throw new ApiError(res.status, d?.code ?? "http_" + res.status, msg);
    }
    return data as T;
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
    lookup: (ticker: string) => req<Record<string, unknown>>("GET", `/stocks/${enc(ticker)}/lookup`),
    valuations: (ticker: string) => req<StockValuations>("GET", `/stocks/${enc(ticker)}/valuations`),
    prices: (ticker: string, days = 365) => req<PriceSeries>("GET", `/stocks/${enc(ticker)}/prices${q({ days })}`),
    excelUrl: (runId: string) => `${base}/runs/${enc(runId)}/excel`,

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
