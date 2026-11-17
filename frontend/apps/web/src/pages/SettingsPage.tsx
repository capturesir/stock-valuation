import { ApiError, type AiSettingsIn, type PriceRow } from "@sv/api-client";
import { LOCALES, type Locale } from "@sv/i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useIsAdmin } from "../lib/session";

type Preset = { id: string; label: string; base_url: string; model: string };

export function SettingsPage() {
  const { t, f, locale, setLocale } = useI18n();
  const qc = useQueryClient();
  const ai = useQuery({ queryKey: ["ai-settings"], queryFn: api.aiSettings });
  const prices = useQuery({ queryKey: ["prices-table"], queryFn: api.priceTable });
  const usage = useQuery({ queryKey: ["usage"], queryFn: () => api.usage() });
  const [form, setForm] = useState<Partial<AiSettingsIn>>({});
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const isAdmin = useIsAdmin();

  useEffect(() => {
    // first visit: nothing saved yet, so start from the selected provider's preset
    const preset = (ai.data?.presets as Preset[] | undefined)?.find((p) => p.id === ai.data?.provider);
    if (ai.data) setForm({ provider: ai.data.provider, base_url: ai.data.base_url || preset?.base_url || "", model: ai.data.model || preset?.model || "",
      budget_per_run_usd: ai.data.budget_per_run_usd, budget_monthly_usd: ai.data.budget_monthly_usd, over_budget: ai.data.over_budget });
  }, [ai.data]);

  const save = useMutation({
    mutationFn: (extra: Partial<AiSettingsIn> = {}) => api.saveAiSettings({ ...form, api_key: key || undefined, ...extra }),
    onSuccess: (d) => { setKey(""); qc.setQueryData(["ai-settings"], d); void qc.invalidateQueries({ queryKey: ["plan"] }); setMsg({ kind: "ok", text: t("common.saved") }); },
    onError: (e) => setMsg({ kind: "err", text: e instanceof ApiError ? e.message : String(e) }),
  });
  const test = useMutation({
    mutationFn: api.testAi,
    onSuccess: (r) => setMsg(r.ok
      ? { kind: "ok", text: t("settings.testOk", { seconds: r.seconds, tokens: (r.tokens_in ?? 0) + (r.tokens_out ?? 0), cost: r.priced ? f.usd(r.cost_usd) : "—" }) }
      : { kind: "err", text: t("settings.testFail", { error: r.error ?? "" }) }),
    onError: (e) => setMsg({ kind: "err", text: t("settings.testFail", { error: e instanceof ApiError ? e.message : String(e) }) }),
  });
  const savePrice = useMutation({ mutationFn: (row: PriceRow) => api.savePrice(row), onSuccess: () => void qc.invalidateQueries({ queryKey: ["prices-table"] }) });

  const presets = (ai.data?.presets ?? []) as Preset[];
  const pick = (p: Preset) => setForm((s) => ({ ...s, provider: p.id, base_url: p.base_url || s.base_url, model: p.model || s.model }));
  const u = usage.data, maxDay = Math.max(0.000001, ...(u?.daily ?? []).map((d) => d.cost_usd as number));
  const maxTask = Math.max(0.000001, ...(u?.by_task ?? []).map((d) => d.cost_usd as number));
  const maxTicker = Math.max(0.000001, ...(u?.by_ticker ?? []).map((d) => d.cost_usd as number));

  return (
    <main className="main">
      <div><h1 style={{ fontSize: "var(--sv-fs-4xl)" }}>{t("settings.title")}</h1><p className="muted" style={{ margin: "6px 0 0", maxWidth: "70ch" }}>{t("settings.subtitle")}</p></div>
      <div className="settings">
        <nav aria-label={t("settings.title")}>
          {["conn", "prices", "budget", "usage", "prefs", "privacy"].map((s) => <a key={s} href={`#${s}`}>{t(`settings.sec.${s}`)}</a>)}
        </nav>
        <div className="content">
          {!isAdmin && <div className="banner info" role="note">{t("settings.adminOnly")}</div>}
          <fieldset className="plain" disabled={!isAdmin}>
          <section id="conn" className="card" aria-labelledby="conn-h">
            <h2 id="conn-h">{t("settings.sec.conn")}</h2>
            <div role="group" aria-label={t("settings.provider")} className="presets">
              {presets.map((p) => <button key={p.id} type="button" aria-pressed={form.provider === p.id} onClick={() => pick(p)}>{p.label}</button>)}
            </div>
            <div className="fields">
              <label className="field">{t("settings.baseUrl")}<input value={form.base_url ?? ""} onChange={(e) => setForm({ ...form, base_url: e.target.value })} inputMode="url" /></label>
              <label className="field">{t("settings.model")}<input value={form.model ?? ""} onChange={(e) => setForm({ ...form, model: e.target.value })} /></label>
            </div>
            <label className="field">{t("settings.apiKey")}
              <input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off"
                     placeholder={ai.data?.api_key.set ? t("settings.keySet", { length: ai.data.api_key.length, tail: ai.data.api_key.tail }) : t("settings.keyUnset")} />
              <small>{t("settings.keyHelp")}</small>
            </label>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <button className="btn secondary" type="button" onClick={() => test.mutate()} disabled={test.isPending}>{t("settings.test")}</button>
              {ai.data?.api_key.set && <button className="btn secondary" type="button" onClick={() => save.mutate({ clear_api_key: true })}>{t("settings.clearKey")}</button>}
              <span className="spacer" />
              <button className="btn primary" type="button" onClick={() => save.mutate({})} disabled={save.isPending}>{t("common.save")}</button>
            </div>
            {msg && <div role="status" className={`banner ${msg.kind === "ok" ? "info" : "err"}`}>{msg.text}</div>}
          </section>

          <section id="prices" className="card" aria-labelledby="prices-h">
            <h2 id="prices-h">{t("settings.sec.prices")}</h2>
            <div className="tbl-wrap">
              <table className="tbl" style={{ minWidth: 720 }}>
                <thead><tr><th scope="col">{t("settings.price.model")}</th><th scope="col">{t("settings.price.input")}</th><th scope="col">{t("settings.price.cached")}</th>
                  <th scope="col">{t("settings.price.output")}</th><th scope="col" className="l">{t("settings.price.note")}</th><th scope="col">{t("settings.price.checked")}</th></tr></thead>
                <tbody>
                  {(prices.data ?? []).map((p) => (
                    <PriceEditRow key={p.model} row={p} active={p.model === ai.data?.model} onSave={(r) => savePrice.mutate(r)} staleLabel={t("settings.price.stale")} />
                  ))}
                </tbody>
              </table>
            </div>
            <span className="muted small">{t("settings.price.disclaimer")}</span>
          </section>

          <section id="budget" className="card" aria-labelledby="budget-h">
            <h2 id="budget-h">{t("settings.sec.budget")}</h2>
            <div className="fields">
              <label className="field">{t("settings.budget.perRun")}<input type="number" min={0} step={0.05} value={form.budget_per_run_usd ?? 0}
                onChange={(e) => setForm({ ...form, budget_per_run_usd: Number(e.target.value) })} /></label>
              <label className="field">{t("settings.budget.monthly")}<input type="number" min={0} step={0.5} value={form.budget_monthly_usd ?? 0}
                onChange={(e) => setForm({ ...form, budget_monthly_usd: Number(e.target.value) })} /></label>
            </div>
            <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
              <legend className="field" style={{ marginBottom: 6 }}>{t("settings.budget.over")}</legend>
              {(["fallback", "ask"] as const).map((o) => (
                <label key={o} style={{ display: "flex", gap: 8, alignItems: "center", minHeight: 40 }}>
                  <input type="radio" name="over" checked={form.over_budget === o} onChange={() => setForm({ ...form, over_budget: o })} />{t(`settings.budget.${o}`)}
                </label>
              ))}
            </fieldset>
            <div><button className="btn primary" type="button" onClick={() => save.mutate({})}>{t("common.save")}</button></div>
          </section>

          </fieldset>
          <section id="usage" className="card" aria-labelledby="usage-h">
            <h2 id="usage-h">{t("settings.sec.usage")}{u ? ` · ${u.month}` : ""}</h2>
            {u && <>
              <div className="kv">
                <div><span className="muted small">{t("settings.usage.total")}</span><strong>{f.usd(u.total_usd)}</strong>
                  <span className="muted small">{t("settings.usage.ofBudget", { pct: f.pct(u.total_usd / Math.max(u.budget_monthly_usd, 0.0001), 1, false) })}</span></div>
                <div><span className="muted small">{t("settings.usage.runs")}</span><strong>{u.runs}</strong><span className="muted small">{t("settings.usage.runsAi", { n: u.runs_with_ai })}</span></div>
                <div><span className="muted small">{t("settings.usage.tokens")}</span><strong>{f.tokens(u.tokens_in + u.tokens_out)}</strong></div>
                <div><span className="muted small">{t("settings.usage.calibration")}</span><strong>×{u.calibration.toFixed(2)}</strong><span className="muted small">{t("settings.usage.calibrationHint")}</span></div>
              </div>
              {u.total_usd === 0 ? <span className="muted">{t("settings.usage.empty")}</span> : <>
                <strong>{t("settings.usage.daily")}</strong>
                <div className="days" role="img" aria-label={t("settings.usage.daily")}>
                  {u.daily.map((d) => <i key={d.date as string} title={`${d.date} ${f.usd(d.cost_usd as number)}`} style={{ height: `${100 * (d.cost_usd as number) / maxDay}%` }} />)}
                </div>
                <div className="row">
                  <div style={{ flex: "1 1 260px", display: "flex", flexDirection: "column", gap: 8 }}>
                    <strong>{t("settings.usage.byTask")}</strong>
                    {u.by_task.map((x) => <div className="hbar" key={x.task as string}><span>{x.task as string} {t(`task.${x.task}`)}</span>
                      <div className="bar"><i style={{ width: `${100 * (x.cost_usd as number) / maxTask}%` }} /></div><span style={{ textAlign: "right" }}>{f.usd(x.cost_usd as number)}</span></div>)}
                  </div>
                  <div style={{ flex: "1 1 260px", display: "flex", flexDirection: "column", gap: 8 }}>
                    <strong>{t("settings.usage.byTicker")}</strong>
                    {u.by_ticker.map((x) => <div className="hbar" key={x.ticker as string}><span>{x.ticker as string}</span>
                      <div className="bar sec"><i style={{ width: `${100 * (x.cost_usd as number) / maxTicker}%` }} /></div><span style={{ textAlign: "right" }}>{f.usd(x.cost_usd as number)}</span></div>)}
                  </div>
                </div>
              </>}
            </>}
          </section>

          <section id="prefs" className="card" aria-labelledby="prefs-h">
            <h2 id="prefs-h">{t("settings.sec.prefs")}</h2>
            <div className="fields">
              <label className="field">{t("settings.language")}
                <select value={locale} onChange={(e) => setLocale(e.target.value as Locale)}>
                  {LOCALES.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                </select></label>
            </div>
          </section>

          <section id="privacy" className="card" aria-labelledby="privacy-h">
            <h2 id="privacy-h">{t("settings.sec.privacy")}</h2>
            <p style={{ margin: 0, lineHeight: 1.7 }}>{t("settings.privacy.sent")}</p>
            <p style={{ margin: 0, lineHeight: 1.7 }}>{t("settings.privacy.notSent")}</p>
          </section>
        </div>
      </div>
      <footer className="foot">{t("app.disclaimer")}</footer>
    </main>
  );
}

function PriceEditRow({ row, active, onSave, staleLabel }: { row: PriceRow; active: boolean; onSave: (r: PriceRow) => void; staleLabel: string }) {
  const [r, setR] = useState(row);
  useEffect(() => setR(row), [row]);
  const num = (k: "input" | "cached_input" | "output") => (
    <input className="cell-in" type="number" min={0} step={0.001} aria-label={`${row.model} ${k}`} value={r[k] ?? ""}
           onChange={(e) => setR({ ...r, [k]: e.target.value === "" ? null : Number(e.target.value) })}
           onBlur={() => { if (JSON.stringify(r) !== JSON.stringify(row)) onSave({ ...r, checked_on: new Date().toISOString().slice(0, 10) }); }} />
  );
  return (
    <tr style={active ? { background: "var(--sv-primary-weak)" } : undefined}>
      <td><strong>{row.model}</strong></td><td>{num("input")}</td><td>{num("cached_input")}</td><td>{num("output")}</td>
      <td className="l" style={{ whiteSpace: "normal" }}>{row.note}{row.source_url && <> · <a href={row.source_url} target="_blank" rel="noreferrer">↗</a></>}</td>
      <td>{row.checked_on ?? "—"}{row.stale && <span className="tag warn" style={{ marginLeft: 6 }}>{staleLabel}</span>}</td>
    </tr>
  );
}
