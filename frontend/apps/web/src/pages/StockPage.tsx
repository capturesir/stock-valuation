import { ApiError, type RunOut } from "@sv/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { Refresh } from "../components/Icons";
import { rememberTicker } from "../components/Layout";
import { CompanyCard, TickerNotFound } from "../components/CompanyCard";
import { DocumentQaTab } from "../components/DocumentQaTab";
import { FiledData } from "../components/FiledData";
import { RunDrawer } from "../components/RunDrawer";
import { TrendChart, type Range } from "../components/TrendChart";
import { api } from "../lib/api";
import { useCooldown } from "../lib/cooldown";
import { useI18n } from "../lib/i18n";

const TABS = ["overview", "valuation", "statements", "qa", "news", "history"] as const;
type Tab = (typeof TABS)[number];
const SCEN = ["bear", "base", "bull", "static", "floor9"] as const;
const dcf = (r: RunOut | null | undefined, s: string) => r?.methods.find((m) => m.method === "dcf" && m.scenario === s)?.value ?? null;

export function StockPage() {
  const { locale, t, f } = useI18n();
  const params = useParams();
  const ticker = (params.ticker ?? "").toUpperCase();
  const tab: Tab = (TABS as readonly string[]).includes(params.tab ?? "") ? (params.tab as Tab) : "overview";
  const [drawer, setDrawer] = useState(false);
  const [range, setRange] = useState<Range>("year");
  const qc = useQueryClient();
  const profile = useQuery({ queryKey: ["profile", ticker], queryFn: () => api.profile(ticker), enabled: !!ticker,
                              staleTime: 10 * 60e3, retry: false });
  const notFound = profile.error instanceof ApiError && profile.error.code === "ticker_not_found";
  const cool = useCooldown();
  const startCooldown = cool.from;      // stable (useCallback); the cool object itself changes every render
  useEffect(() => { if (profile.error) startCooldown(profile.error); }, [profile.error, startCooldown]);
  const vals = useQuery({ queryKey: ["valuations", ticker], queryFn: () => api.valuations(ticker), enabled: !!ticker && !notFound });
  const prices = useQuery({ queryKey: ["prices", ticker], queryFn: () => api.prices(ticker, 400), enabled: !!ticker, staleTime: 3600e3, retry: false });
  const wl = useQuery({ queryKey: ["watchlist"], queryFn: api.watchlist });
  const add = useMutation({ mutationFn: () => api.addToWatchlist(ticker), onSuccess: () => void qc.invalidateQueries({ queryKey: ["watchlist"] }) });
  const nav = useNavigate();
  // 700 -> 0700.HK: show and bookmark the ticker the backend resolved
  useEffect(() => {
    const resolved = profile.data?.ticker;
    if (resolved && resolved !== ticker) nav(`/stock/${encodeURIComponent(resolved)}${tab === "overview" ? "" : `/${tab}`}`, { replace: true });
  }, [profile.data?.ticker, ticker, tab, nav]);
  useEffect(() => { if (ticker && !notFound) rememberTicker(ticker); }, [ticker, notFound]);

  const v = vals.data, latest = v?.latest, prev = v?.previous, ccy = v?.currency ?? latest?.trading_ccy;
  const gap = latest?.composite_value != null && latest.price ? latest.composite_value / latest.price - 1 : null;
  const inList = wl.data?.some((w) => w.ticker === ticker);

  if (notFound) {
    return (
      <main className="main">
        <span className="muted"><Link to="/" style={{ color: "inherit" }}>{t("nav.watchlist")}</Link> / {ticker}</span>
        <TickerNotFound ticker={ticker} error={profile.error} />
        <footer className="foot">{t("app.disclaimer")}</footer>
      </main>
    );
  }

  return (
    <main className="main">
      <section className="pagehead" aria-label={ticker}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="muted"><Link to="/" style={{ color: "inherit" }}>{t("nav.watchlist")}</Link> / {ticker}</span>
          <h1>{profile.data?.name ?? v?.company ?? ticker}</h1>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <span className="tag neutral">{ticker}</span>
            {profile.data?.exchange && <span className="tag neutral">{profile.data.exchange}</span>}
            {(ccy ?? profile.data?.currency) && <span className="tag neutral">{ccy ?? profile.data?.currency}</span>}
            <span className="tag primary">{t("stock.method.dcf")}</span>
          </div>
        </div>
        <div className="stats">
          {latest && <>
            <div className="stat"><span className="k">{t("stock.price", { date: latest.price_date ?? latest.valuation_date })}</span><span className="v">{f.money(latest.price, ccy)}</span></div>
            <div className="stat"><span className="k">{t("stock.composite")}</span><span className="v primary">{f.money(latest.composite_value, ccy)}</span>
              <span className="small">{gap != null && t(gap >= 0 ? "stock.upside" : "stock.downside", { pct: f.pct(gap) })}</span></div>
          </>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary" type="button" onClick={() => setDrawer(true)}><Refresh size={16} />{t("stock.revalue")}</button>
            {latest?.has_excel && <a className="btn secondary" href={api.excelUrl(latest.id, locale)}>{t("stock.downloadExcel")}</a>}
            {latest?.has_excel && <a className="btn secondary" href={api.reportUrl(latest.id, locale)}>{t("stock.downloadReport")}</a>}
            {!inList && <button className="btn secondary" type="button" onClick={() => add.mutate()}>{t("stock.addToWatchlist")}</button>}
          </div>
        </div>
      </section>

      <nav className="tabs" aria-label={t("stock.tabs")}>
        {TABS.map((k) => <NavLink key={k} to={`/stock/${ticker}/${k}`} className={tab === k ? "active" : ""} end>{t(`stock.tab.${k}`)}</NavLink>)}
      </nav>

      {tab === "overview" && profile.isLoading && <p className="muted">{t("profile.loading")}</p>}
      {tab === "overview" && profile.error && !notFound && (
        <div className="banner warn" role="alert">
          <span style={{ flex: "1 1 240px" }}>{(profile.error as Error).message}</span>
          <button className="btn secondary small" type="button" disabled={cool.left > 0} onClick={() => void profile.refetch()}>
            {cool.left > 0 ? t("common.retryIn", { seconds: cool.left }) : t("common.retry")}</button>
        </div>
      )}
      {tab === "overview" && profile.data && !latest && <CompanyCard p={profile.data} />}
      {vals.isLoading && <p className="muted">{t("common.loading")}</p>}
      {vals.error && <div className="banner err" role="alert">{t("common.error", { message: (vals.error as Error).message })}</div>}
      {v && !latest && tab !== "history" && tab !== "statements" && tab !== "qa" && (
        <div className="card"><h2>{t("stock.noValuation", { ticker })}</h2><p className="muted" style={{ margin: 0 }}>{t("stock.noValuationHint")}</p>
          <div><button className="btn primary" type="button" onClick={() => setDrawer(true)}>{t("stock.revalue")}</button></div></div>
      )}

      {v && latest && tab === "overview" && (
        <>
          <div className="row">
            <section className="card col-main" aria-labelledby="trend-h">
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
                <div><h2 id="trend-h">{t("stock.trend.title")}</h2><span className="muted">{t("stock.trend.subtitle", { n: v.runs.filter((r) => r.status === "done").length })}</span></div>
                <div className="seg" role="group" aria-label={t("stock.trend.title")}>
                  {(["near5", "year", "all"] as Range[]).map((r) => <button key={r} type="button" aria-pressed={range === r} onClick={() => setRange(r)}>{t(`stock.range.${r}`)}</button>)}
                </div>
              </div>
              <div className="legend">
                <span><i style={{ height: 2, background: "var(--sv-chart-price)" }} />{t("stock.trend.price")}</span>
                <span><i style={{ height: 3, background: "var(--sv-chart-dcf)" }} />{t("stock.trend.dcf")}</span>
                <span><i style={{ height: 10, background: "var(--sv-chart-band)", borderRadius: 2 }} />{t("stock.trend.band")}</span>
              </div>
              <TrendChart runs={v.runs} prices={prices.data} range={range} currency={ccy} />
              {(prices.isError || prices.data?.error) && <span className="muted small">{t("stock.trend.noPrices")}</span>}
            </section>
            <aside className="card col-side" aria-labelledby="comp-h">
              <h2 id="comp-h" style={{ fontSize: "var(--sv-fs-lg)", color: "var(--sv-ink-700)" }}>{t("stock.composite")}</h2>
              <span className="big">{f.money(latest.composite_value, ccy)}</span>
              {gap != null && <span>{t(gap >= 0 ? "stock.upside" : "stock.downside", { pct: f.pct(gap) })}</span>}
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}><span>{t("stock.method.dcf")} · {t("stock.weight", { pct: "100%" })}</span><strong>{f.value(dcf(latest, "base"))}</strong></div>
                <div className="bar"><i style={{ width: "100%" }} /></div>
              </div>
              <span className="muted small">{t("stock.compositeNote")}</span>
              <div style={{ borderTop: "1px solid var(--sv-line-faint)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                <strong>{t("stock.compare.title")}</strong>
                {prev ? <>
                  <span style={{ fontSize: 20, fontWeight: 700 }}>{t("stock.compare.change", { from: f.value(dcf(prev, "base")), to: f.value(dcf(latest, "base")) })}{" "}
                    <span className={(dcf(latest, "base") ?? 0) >= (dcf(prev, "base") ?? 0) ? "pos" : "neg"} style={{ fontSize: 14 }}>
                      {f.delta((dcf(latest, "base") ?? 0) - (dcf(prev, "base") ?? 0))} ({f.pct((dcf(latest, "base") ?? 0) / (dcf(prev, "base") || 1) - 1)})</span></span>
                  <span className="muted small">{prev.valuation_date} → {latest.valuation_date} · {t("stock.compare.attribution")}</span>
                </> : <span className="muted small">{t("stock.compare.none")}</span>}
              </div>
            </aside>
          </div>
          {latest.data_gaps.length > 0 && (
            <section className="card" aria-labelledby="gaps-h">
              <h2 id="gaps-h">{t("stock.gaps")}</h2>
              <ul style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6, lineHeight: 1.6 }}>
                {latest.data_gaps.map((g, i) => <li key={i}>{g}</li>)}
              </ul>
            </section>
          )}
        </>
      )}

      {v && latest && tab === "valuation" && (
        <>
          <section className="card" aria-labelledby="scen-h">
            <h2 id="scen-h">{t("stock.method.dcf")}</h2>
            <div className="scen">
              {SCEN.map((s) => (
                <div key={s} className={s === "base" ? "base" : ""}>
                  <span className="muted">{t(`stock.scenario.${s}`)}</span><strong>{f.value(dcf(latest, s))}</strong>
                </div>
              ))}
            </div>
          </section>
          <section className="card">
            <div className="kv">
              <div><span className="muted small">{t("stock.wacc")}</span><strong>{f.pct(latest.wacc, 2, false)}</strong></div>
              <div><span className="muted small">{t("stock.g")}</span><strong>{f.pct(latest.terminal_g, 2, false)}</strong></div>
              <div><span className="muted small">{t("stock.verify")}</span><strong>{latest.verify_pass == null ? "—" : t(latest.verify_pass ? "stock.verify.pass" : "stock.verify.fail")}</strong></div>
              <div><span className="muted small">{t("history.col.mode")}</span><strong>{t(`run.mode.${latest.ai_mode}`)}</strong></div>
              <div><span className="muted small">{t("run.statements")}</span>
                <strong>{t(`run.source.${latest.statement_source ?? "yahoo"}`)}{latest.ruleset_version ? ` · ${latest.ruleset_version}` : ""}</strong></div>
              {latest.statement_source === "sec_standard" && (
                <div><span className="muted small">{t("adj.plan")}</span>
                  <strong>{planText(latest.adjustment_plan as { kind: string; version: number; label: string } | null | undefined, t)}</strong></div>
              )}
            </div>
          </section>
        </>
      )}

      {tab === "statements" && <FiledData ticker={ticker} />}
      {tab === "qa" && <DocumentQaTab ticker={ticker} />}
      {tab === "news" && <div className="banner info">{t("stock.phaseNote", { what: t("stock.phase.news") })}</div>}

      {v && tab === "history" && (
        <section className="card" style={{ padding: 0, overflow: "hidden" }} aria-label={t("stock.tab.history")}>
          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 900 }}>
              <thead><tr>
                <th scope="col">{t("history.col.time")}</th><th scope="col" className="l">{t("history.col.trigger")}</th><th scope="col" className="l">{t("history.col.mode")}</th>
                <th scope="col">{t("history.col.dcf")}</th><th scope="col">{t("history.col.composite")}</th><th scope="col">{t("history.col.price")}</th>
                <th scope="col">{t("history.col.cost")}</th><th scope="col">{t("history.col.verify")}</th><th scope="col">{t("history.col.file")}</th>
              </tr></thead>
              <tbody>
                {[...v.runs].reverse().map((r) => (
                  <tr key={r.id}>
                    <td>{f.datetime(r.run_at)}</td>
                    <td className="l">{t(`history.trigger.${r.trigger}`)}</td>
                    <td className="l">{t(`run.mode.${r.ai_mode}`)}</td>
                    {r.status === "done" ? <>
                      <td>{f.value(dcf(r, "base"))}</td><td><strong>{f.value(r.composite_value)}</strong></td><td>{f.money(r.price, r.trading_ccy ?? ccy)}</td>
                    </> : <td colSpan={3} className="l neg" style={{ whiteSpace: "normal" }}>{r.status === "failed" ? t("history.status.failed", { error: r.error ?? "" }) : t("history.status.cancelled")}</td>}
                    <td>{f.usd(r.ai_cost_usd)}</td>
                    <td>{r.verify_pass == null ? "—" : r.verify_pass ? "✓" : "✗"}</td>
                    <td>{r.has_excel ? <a href={api.excelUrl(r.id)}>Excel</a> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <footer className="foot">{t("app.disclaimer")}</footer>
      {drawer && <RunDrawer ticker={ticker} onClose={() => setDrawer(false)} />}
    </main>
  );
}


/** "Central plan v3", "Ann's personal plan v5" or "System default" (D12: shared results say whose plan). */
function planText(p: { kind: string; version: number; label: string } | null | undefined, t: (k: string, v?: Record<string, string | number>) => string) {
  if (!p) return t("adj.using.none");
  if (p.kind === "central") return t("run.plan.central", { v: p.version });
  return t("run.plan.personal", { name: p.label.replace(/^personal:/, "").replace(/ v\d+$/, ""), v: p.version });
}
