import { ApiError, type WatchlistEntry } from "@sv/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Plus } from "../components/Icons";
import { TickerNotFound } from "../components/CompanyCard";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";

const dcf = (e: WatchlistEntry, s: string) => e.latest?.methods.find((m) => m.method === "dcf" && m.scenario === s)?.value ?? null;

/** Position of x on a bear–bull track drawn from 15% to 85% of the cell (clamped to the cell). */
function pos(x: number | null, bear: number | null, bull: number | null): number {
  if (x == null || bear == null || bull == null || bull <= bear) return 50;
  return Math.max(2, Math.min(98, 15 + ((x - bear) / (bull - bear)) * 70));
}

export function WatchlistPage() {
  const { t, f } = useI18n();
  const qc = useQueryClient();
  const [ticker, setTicker] = useState("");
  const list = useQuery({ queryKey: ["watchlist"], queryFn: api.watchlist });
  const usage = useQuery({ queryKey: ["usage"], queryFn: () => api.usage() });
  const add = useMutation({
    mutationFn: async (tk: string) => { const p = await api.profile(tk); const w = await api.addToWatchlist(p.ticker); return { ...w, company: w.company ?? p.name }; },
    onSuccess: () => { setTicker(""); void qc.invalidateQueries({ queryKey: ["watchlist"] }); },
  });
  const remove = useMutation({
    mutationFn: (tk: string) => api.removeFromWatchlist(tk),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (ticker.trim()) add.mutate(ticker.trim()); };
  const items = list.data ?? [];
  const alerts = items.filter((i) => i.status === "below_bear" || i.status === "above_bull" || i.status === "never_valued");

  return (
    <main className="main">
      <div className="pagehead">
        <div>
          <h1>{t("watchlist.title")}</h1>
          <p className="muted" style={{ margin: "6px 0 0" }}>{t("watchlist.subtitle", { n: items.length })}</p>
        </div>
        <form onSubmit={submit} style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
          <label className="field" style={{ minWidth: 220 }}>{t("watchlist.addLabel")}
            <input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder={t("watchlist.addPlaceholder")} autoCapitalize="characters" />
          </label>
          <button className="btn primary" type="submit" disabled={add.isPending}><Plus size={16} />{t("common.add")}</button>
        </form>
      </div>
      {add.error && (add.error instanceof ApiError && add.error.code === "ticker_not_found"
        ? <TickerNotFound ticker={ticker.trim().toUpperCase()} error={add.error} />
        : <div className="banner err" role="alert">{add.error instanceof ApiError ? add.error.message : String(add.error)}</div>)}
      {add.data && !add.error && <div className="banner info" role="status">{t("watchlist.added", { ticker: add.data.ticker, name: add.data.company ?? "" })}</div>}

      <div className="row">
        <section className="card col-main" aria-labelledby="alerts-h">
          <h2 id="alerts-h">{t("watchlist.alerts")}</h2>
          {alerts.length === 0 && <span className="muted">{t("watchlist.noAlerts")}</span>}
          {alerts.map((a) => (
            <div key={a.ticker} className={`banner ${a.status === "below_bear" ? "err" : "warn"}`}>
              <span className={`tag ${a.status === "below_bear" ? "bad" : "warn"}`}>{t(`watchlist.status.${a.status}`)}</span>
              <span style={{ flex: "1 1 240px" }}><strong>{a.ticker}</strong> {a.company ?? ""}</span>
              <Link to={`/stock/${a.ticker}`} style={{ fontWeight: 600, padding: "10px 4px" }}>{t("stock.revalue")}</Link>
            </div>
          ))}
        </section>
        <section className="card col-side" aria-labelledby="cost-h">
          <h2 id="cost-h">{t("watchlist.monthCost")}</h2>
          {usage.data && <>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span style={{ fontSize: 32, fontWeight: 700 }}>{f.usd(usage.data.total_usd)}</span>
              <span className="muted">/ {f.usd(usage.data.budget_monthly_usd)}</span>
            </div>
            <div className="bar"><i style={{ width: `${Math.min(100, 100 * usage.data.total_usd / Math.max(usage.data.budget_monthly_usd, 0.0001))}%` }} /></div>
            <span className="small">{t("watchlist.monthCostHint", { runs: usage.data.runs, ai: usage.data.runs_with_ai })}</span>
          </>}
          <Link to="/settings" className="small">{t("nav.settings")} →</Link>
        </section>
      </div>

      <section className="card" style={{ padding: 0, overflow: "hidden" }} aria-label={t("watchlist.title")}>
        {list.isLoading && <p className="muted" style={{ padding: 20 }}>{t("common.loading")}</p>}
        {list.error && <div className="banner err" style={{ margin: 20 }}>{t("common.error", { message: String(list.error) })}</div>}
        {!list.isLoading && items.length === 0 && <p className="muted" style={{ padding: 20 }}>{t("watchlist.empty")}</p>}
        {items.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 980 }}>
              <thead><tr>
                <th scope="col">{t("watchlist.col.stock")}</th><th scope="col">{t("watchlist.col.price")}</th>
                <th scope="col">{t("watchlist.col.fair")}</th><th scope="col">{t("watchlist.col.gap")}</th>
                <th scope="col" style={{ textAlign: "center" }}>{t("watchlist.col.range")}</th>
                <th scope="col">{t("watchlist.col.last")}</th><th scope="col">{t("watchlist.col.status")}</th><th scope="col"><span className="sr-only">{t("common.remove")}</span></th>
              </tr></thead>
              <tbody>
                {items.map((e) => {
                  const l = e.latest, bear = dcf(e, "bear"), bull = dcf(e, "bull");
                  const gap = l?.composite_value != null && l.price ? l.composite_value / l.price - 1 : null;
                  return (
                    <tr key={e.ticker}>
                      <td><Link className="name" to={`/stock/${e.ticker}`}><strong>{e.company ?? e.ticker}</strong>{e.company && <span>{e.ticker}</span>}</Link></td>
                      <td>{f.money(l?.price, l?.trading_ccy)}</td>
                      <td><strong>{f.value(l?.composite_value)}</strong></td>
                      <td className={gap == null ? "" : gap >= 0 ? "pos" : "neg"}>{f.pct(gap)}</td>
                      <td>{l && (
                        <div className="range" aria-hidden="true">
                          <div className="b" /><div className="f" style={{ left: `${pos(l.composite_value, bear, bull)}%` }} />
                          <div className="p" style={{ left: `${pos(l.price, bear, bull)}%`, background: e.status === "below_bear" ? "var(--sv-danger)" : undefined }} />
                        </div>)}</td>
                      <td>{l?.valuation_date ?? "—"}</td>
                      <td><span className={`tag ${e.status === "ok" ? "neutral" : e.status === "below_bear" ? "bad" : "warn"}`}>{t(`watchlist.status.${e.status}`)}</span></td>
                      <td><button className="btn secondary small" type="button" onClick={() => remove.mutate(e.ticker)}>{t("common.remove")}</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <footer className="foot">{t("app.disclaimer")}</footer>
    </main>
  );
}
