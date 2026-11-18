import { ApiError, type AdminUserIn, type AdminUserUpdate, type StatementsSyncResult, type UserOut } from "@sv/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router-dom";
import { api } from "../lib/api";
import { useBranding } from "../lib/branding";
import { useI18n } from "../lib/i18n";
import { useMe } from "../lib/session";

type Msg = { kind: "ok" | "err"; text: string } | null;
const errText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

export function AdminPage() {
  const { t } = useI18n();
  const me = useMe().data;
  if (me && me.role !== "admin") return <Navigate to="/" replace />;
  return (
    <main className="main">
      <div><h1 style={{ fontSize: "var(--sv-fs-4xl)" }}>{t("admin.title")}</h1>
        <p className="muted" style={{ margin: "6px 0 0" }}>{t("admin.subtitle")}</p></div>
      <Users />
      <Branding />
      <Market />
      <DataSources />
      <Limits />
      <Ops />
      <footer className="foot">{t("app.disclaimer")}</footer>
    </main>
  );
}

function Users() {
  const { t, f } = useI18n();
  const qc = useQueryClient();
  const me = useMe().data;
  const users = useQuery({ queryKey: ["admin-users"], queryFn: api.users });
  const [form, setForm] = useState<AdminUserIn>({ email: "", display_name: "", role: "member", password: "" });
  const [msg, setMsg] = useState<Msg>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["admin-users"] });
  const add = useMutation({
    mutationFn: () => api.addUser(form),
    onSuccess: (u) => { setForm({ email: "", display_name: "", role: "member", password: "" }); setMsg({ kind: "ok", text: `${u.email} ✓` }); refresh(); },
    onError: (e) => setMsg({ kind: "err", text: errText(e) }),
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: AdminUserUpdate }) => api.updateUser(id, body),
    onSuccess: () => { setMsg({ kind: "ok", text: t("common.saved") }); refresh(); },
    onError: (e) => setMsg({ kind: "err", text: errText(e) }),
  });
  const reset = (u: UserOut) => {
    const pw = window.prompt(t("admin.users.resetPrompt", { email: u.email }));
    if (pw) update.mutate({ id: u.id, body: { password: pw } });
  };
  const when = (iso?: string | null) => (iso ? f.datetime(iso) : t("admin.users.never"));

  return (
    <section className="card" aria-labelledby="users-h">
      <h2 id="users-h">{t("admin.sec.users")}</h2>
      <div className="tbl-wrap">
        <table className="tbl" style={{ minWidth: 760 }}>
          <thead><tr><th scope="col">{t("admin.users.name")}</th><th scope="col" className="l">{t("auth.email")}</th>
            <th scope="col" className="l">{t("account.role")}</th><th scope="col" className="l">{t("admin.users.status")}</th>
            <th scope="col">{t("admin.users.lastLogin")}</th><th scope="col">{t("admin.users.actions")}</th></tr></thead>
          <tbody>
            {(users.data ?? []).map((u) => (
              <tr key={u.id}>
                <td><strong>{u.display_name}</strong> {u.id === me?.id && <span className="muted small">{t("admin.users.you")}</span>}</td>
                <td className="l">{u.email}</td>
                <td className="l">{t(`account.role.${u.role}`)}</td>
                <td className="l"><span className={`tag ${u.is_active ? "ok" : "neutral"}`}>{t(u.is_active ? "admin.users.active" : "admin.users.disabled")}</span></td>
                <td>{when(u.last_login_at)}</td>
                <td><div className="row-actions">
                  <button className="btn small secondary" type="button" onClick={() => update.mutate({ id: u.id, body: { role: u.role === "admin" ? "member" : "admin" } })}>
                    {t(u.role === "admin" ? "admin.users.makeMember" : "admin.users.makeAdmin")}</button>
                  <button className="btn small secondary" type="button" onClick={() => reset(u)}>{t("admin.users.resetPassword")}</button>
                  {u.id !== me?.id && <button className={`btn small ${u.is_active ? "danger" : "secondary"}`} type="button"
                    onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}>
                    {t(u.is_active ? "admin.users.disable" : "admin.users.enable")}</button>}
                </div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form className="fields" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }} aria-label={t("admin.users.add")}>
        <label className="field">{t("auth.email")}<input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
        <label className="field">{t("admin.users.name")}<input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></label>
        <label className="field">{t("account.role")}
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as AdminUserIn["role"] })}>
            <option value="member">{t("account.role.member")}</option><option value="admin">{t("account.role.admin")}</option>
          </select></label>
        <label className="field">{t("auth.password")}<input type="password" autoComplete="new-password" required minLength={8} value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
        <div style={{ alignSelf: "flex-end" }}><button className="btn primary" type="submit" disabled={add.isPending}>{t("admin.users.add")}</button></div>
      </form>
      {msg && <div role="status" className={`banner ${msg.kind === "ok" ? "info" : "err"}`}>{msg.text}</div>}
    </section>
  );
}

function Branding() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const b = useBranding();
  const [name, setName] = useState(b.name);
  const [msg, setMsg] = useState<Msg>(null);
  useEffect(() => setName(b.name), [b.name]);
  const done = (d: unknown) => { qc.setQueryData(["branding"], d); setMsg({ kind: "ok", text: t("common.saved") }); };
  const fail = (e: unknown) => setMsg({ kind: "err", text: errText(e) });
  const saveName = useMutation({ mutationFn: () => api.saveBrandingName(name.trim()), onSuccess: done, onError: fail });
  const upload = useMutation({ mutationFn: ({ v, file }: { v: "light" | "dark"; file: File }) => api.uploadLogo(v, file, file.name), onSuccess: done, onError: fail });
  const remove = useMutation({ mutationFn: (v: "light" | "dark") => api.removeLogo(v), onSuccess: done, onError: fail });
  const data = qc.getQueryData<{ logo_light_url?: string | null; logo_dark_url?: string | null }>(["branding"]);
  // the API mirrors light into dark when only one is set; show what was actually uploaded
  const own = { light: data?.logo_light_url ?? null, dark: data?.logo_dark_url && data.logo_dark_url !== data.logo_light_url ? data.logo_dark_url : null };

  return (
    <section className="card" aria-labelledby="brand-h">
      <h2 id="brand-h">{t("admin.sec.branding")}</h2>
      <form className="fields" onSubmit={(e: FormEvent) => { e.preventDefault(); saveName.mutate(); }}>
        <label className="field">{t("admin.brand.name")}<input value={name} maxLength={40} required onChange={(e) => setName(e.target.value)} />
          <small>{t("admin.brand.nameHelp")}</small></label>
        <div style={{ alignSelf: "flex-end" }}><button className="btn primary" type="submit">{t("common.save")}</button></div>
      </form>
      <div className="logo-slots">
        {(["light", "dark"] as const).map((v) => (
          <LogoSlot key={v} variant={v} url={own[v]} label={t(v === "light" ? "admin.brand.logoLight" : "admin.brand.logoDark")}
                    onFile={(file) => upload.mutate({ v, file })} onRemove={() => remove.mutate(v)} />
        ))}
      </div>
      <small className="muted small">{t("admin.brand.logoHelp")}</small>
      {msg && <div role="status" className={`banner ${msg.kind === "ok" ? "info" : "err"}`}>{msg.text}</div>}
    </section>
  );
}

function LogoSlot({ variant, url, label, onFile, onRemove }: { variant: "light" | "dark"; url: string | null; label: string;
                                                               onFile: (f: File) => void; onRemove: () => void }) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="logo-slot">
      <span className="field">{label}</span>
      <div className={`logo-preview ${variant}`}>{url ? <img src={api.assetUrl(url)} alt={label} /> : <span className="muted small">{t("admin.brand.none")}</span>}</div>
      <div className="row-actions">
        <input ref={input} type="file" accept="image/png,image/svg+xml" hidden
               onChange={(e) => { const file = e.target.files?.[0]; if (file) onFile(file); e.target.value = ""; }} />
        <button className="btn small secondary" type="button" onClick={() => input.current?.click()}>{t("admin.brand.upload")}</button>
        {url && <button className="btn small danger" type="button" onClick={onRemove}>{t("admin.brand.remove")}</button>}
      </div>
    </div>
  );
}

function Market() {
  const { t, f } = useI18n();
  const status = useQuery({ queryKey: ["market-status"], queryFn: api.marketStatus });
  const secs = useQuery({ queryKey: ["securities"], queryFn: api.securities });
  const [msg, setMsg] = useState<Msg>(null);
  const run = useMutation({ mutationFn: api.updateAllMarket, onSuccess: () => setMsg({ kind: "ok", text: t("admin.market.started") }),
                            onError: (e) => setMsg({ kind: "err", text: errText(e) }) });
  const st = status.data;
  const last = st?.last_run as { finished_at?: string; synced?: number; failed?: unknown[] } | null | undefined;
  return (
    <section className="card" aria-labelledby="market-h">
      <h2 id="market-h">{t("admin.sec.market")}</h2>
      <p className="muted" style={{ margin: 0 }}>{t("admin.market.intro")}</p>
      {st && <p className="small" style={{ margin: 0 }}>
        {st.scheduler === "off" ? t("admin.market.off")
          : t("admin.market.schedule", { times: st.times.join("、"), tz: st.timezone, next: st.next_runs[0]?.replace("T", " ").slice(0, 16) ?? "—" })}
        {last?.finished_at && <> · {t("admin.market.lastRun", { at: f.datetime(last.finished_at), ok: last.synced ?? 0, failed: last.failed?.length ?? 0 })}</>}
      </p>}
      <div className="tbl-wrap">
        <table className="tbl" style={{ minWidth: 720 }}>
          <thead><tr><th scope="col">{t("admin.market.ticker")}</th><th scope="col" className="l">{t("admin.users.name")}</th>
            <th scope="col">{t("admin.market.range")}</th><th scope="col">{t("admin.market.bars")}</th>
            <th scope="col" className="l">{t("admin.market.check")}</th><th scope="col">{t("admin.market.updated")}</th></tr></thead>
          <tbody>
            {(secs.data ?? []).map((s) => {
              const c = (s.checks as { total_return?: { status: string; max_deviation?: number; note_code?: string; note?: string } } | null)?.total_return;
              const note = c?.note_code ? t(`market.note.${c.note_code}`) : c?.note;
              return (
                <tr key={s.ticker}>
                  <td><strong>{s.ticker}</strong></td>
                  <td className="l" style={{ whiteSpace: "normal" }}>{s.name ?? "—"} <span className="muted small">{s.currency ?? ""}</span></td>
                  <td>{s.first_date ?? "—"} – {s.last_date ?? "—"}</td>
                  <td>{s.bars.toLocaleString()}</td>
                  <td className="l">
                    {s.last_error ? <span className="tag bad" title={s.last_error}>{t("admin.market.error")}</span>
                      : c?.status === "ok" ? <span className="tag ok">{t("admin.market.ok")}</span>
                      : c?.status === "warn" ? <span className="tag warn" title={note}>{t("admin.market.warn", { pct: f.pct(c.max_deviation ?? 0, 1, false) })}</span>
                      : c?.status === "explained" ? <span className="tag ok" title={note}>{t("admin.market.explained", { pct: f.pct(c.max_deviation ?? 0, 1, false) })}</span>
                      : <span className="muted">—</span>}
                  </td>
                  <td>{s.updated_at ? f.datetime(s.updated_at) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div><button className="btn secondary" type="button" onClick={() => run.mutate()} disabled={run.isPending}>{t("admin.market.updateNow")}</button></div>
      {msg && <div role="status" className={`banner ${msg.kind === "ok" ? "info" : "err"}`}>{msg.text}</div>}
    </section>
  );
}

function DataSources() {
  const { t, f } = useI18n();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["data-sources"], queryFn: api.dataSources });
  const [org, setOrg] = useState("");
  const [email, setEmail] = useState("");
  const [ticker, setTicker] = useState("");
  const [saveMsg, setSaveMsg] = useState<Msg>(null);
  const [syncErr, setSyncErr] = useState<string | null>(null);
  const [result, setResult] = useState<StatementsSyncResult | null>(null);
  useEffect(() => { if (q.data) { setOrg(q.data.sec_organisation ?? ""); setEmail(q.data.sec_email ?? ""); } }, [q.data]);
  const save = useMutation({ mutationFn: () => api.saveDataSources({ sec_organisation: org.trim() || null, sec_email: email.trim() || null }),
                             onMutate: () => setSaveMsg(null),
                             onSuccess: (d) => { qc.setQueryData(["data-sources"], d);
                               setSaveMsg({ kind: "ok", text: d.sec_ready ? t("admin.sources.saved", { contact: d.sec_contact ?? "" }) : t("admin.sources.cleared") }); },
                             onError: (e) => setSaveMsg({ kind: "err", text: errText(e) }) });
  const sync = useMutation({ mutationFn: (tk: string) => api.syncStatements(tk),
                             onMutate: () => { setSyncErr(null); setResult(null); },
                             onSuccess: (r) => { setResult(r); for (const k of ["filer", "filed-concepts", "filed-facts"]) void qc.invalidateQueries({ queryKey: [k, r.ticker] }); },
                             onError: (e) => setSyncErr(errText(e)) });
  const ready = !!q.data?.sec_ready;
  return (
    <section className="card" aria-labelledby="sources-h">
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <h2 id="sources-h">{t("admin.sec.sources")}</h2>
        {q.data && <span className={`tag ${ready ? "ok" : "warn"}`}>{t(ready ? "admin.sources.ready" : "admin.sources.notReady")}</span>}
      </div>
      <p className="muted" style={{ margin: 0 }}>{t("admin.sources.intro")}</p>
      <form className="fields" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        <label className="field" style={{ flex: "1 1 220px" }}>{t("admin.sources.secOrganisation")}
          <input value={org} onChange={(e) => setOrg(e.target.value)} placeholder="Example Research" autoComplete="organization" maxLength={120} />
        </label>
        <label className="field" style={{ flex: "1 1 220px" }}>{t("admin.sources.secEmail")}
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="admin@example.com" autoComplete="email" maxLength={254} />
        </label>
        <div style={{ alignSelf: "flex-end" }}><button className="btn primary" type="submit" disabled={save.isPending}>{t("common.save")}</button></div>
      </form>
      <p className="small muted" style={{ margin: 0 }}>{t("admin.sources.secContactHelp")}</p>
      {saveMsg && <div role="status" className={`banner ${saveMsg.kind === "ok" ? "info" : "err"}`}>{saveMsg.text}</div>}
      <form className="fields" onSubmit={(e: FormEvent) => { e.preventDefault(); if (ticker.trim()) sync.mutate(ticker.trim()); }}>
        <label className="field">{t("admin.sources.syncTicker")}
          <input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="NVDA" autoComplete="off" />
        </label>
        <div style={{ alignSelf: "flex-end" }}>
          <button className="btn secondary" type="submit" disabled={sync.isPending || !ready}>
            {sync.isPending ? t("admin.sources.syncing") : t("admin.sources.sync")}</button>
        </div>
      </form>
      {q.data && !ready && <p className="small muted" style={{ margin: 0 }}>{t("admin.sources.needContact")}</p>}
      {q.data && ready && <p className="small muted" style={{ margin: 0 }}>{(() => {
        const r = q.data.statements_last_run as { finished_at?: string; checked?: number; updated?: unknown[]; failed?: unknown[] } | null | undefined;
        return r?.finished_at ? t("admin.sources.daily", { at: f.datetime(r.finished_at), checked: r.checked ?? 0, updated: r.updated?.length ?? 0, failed: r.failed?.length ?? 0 })
          : t("admin.sources.dailyNone");
      })()}</p>}
      {result && <div role="status" className="banner info">
        <span style={{ flex: "1 1 240px" }}>
          {t(result.unchanged ? "admin.sources.unchanged" : "admin.sources.synced", {
            ticker: result.ticker, name: result.name ?? "", filings: result.filings_added, facts: result.facts_added,
            total: result.facts_total.toLocaleString() })}
          {!result.has_xbrl && <> {t("admin.sources.noXbrl")}</>}
        </span>
        {result.has_xbrl && <Link to={`/stock/${encodeURIComponent(result.ticker)}/statements`} style={{ fontWeight: 600 }}>{t("admin.sources.view", { ticker: result.ticker })}</Link>}
      </div>}
      {syncErr && <div role="alert" className="banner err">{syncErr}</div>}
    </section>
  );
}

const LIMIT_KEYS = ["lookup_per_minute", "prices_per_minute", "valuations_per_hour", "concurrent_valuations_per_user",
                    "queue_max", "revalue_cooldown_hours"] as const;

function Limits() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-limits"], queryFn: api.limits });
  const [form, setForm] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState<Msg>(null);
  useEffect(() => { if (q.data) setForm(Object.fromEntries(LIMIT_KEYS.map((k) => [k, q.data[k]]))); }, [q.data]);
  const save = useMutation({ mutationFn: () => api.saveLimits(form), onSuccess: (d) => { qc.setQueryData(["admin-limits"], d); setMsg({ kind: "ok", text: t("common.saved") }); },
                             onError: (e) => setMsg({ kind: "err", text: errText(e) }) });
  const defaults = (q.data?.defaults ?? {}) as Record<string, number>;
  return (
    <section className="card" aria-labelledby="limits-h">
      <h2 id="limits-h">{t("admin.sec.limits")}</h2>
      <p className="muted" style={{ margin: 0 }}>{t("admin.limits.intro")}</p>
      <form className="fields" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        {LIMIT_KEYS.map((k) => (
          <label key={k} className="field">{t(`admin.limits.${k}`)}
            <input type="number" min={k === "revalue_cooldown_hours" ? 0 : 1} value={form[k] ?? ""}
                   onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })} />
            <small>{t("admin.limits.default", { n: defaults[k] ?? "—" })}</small>
          </label>
        ))}
        <div style={{ alignSelf: "flex-end" }}><button className="btn primary" type="submit">{t("common.save")}</button></div>
      </form>
      {msg && <div role="status" className={`banner ${msg.kind === "ok" ? "info" : "err"}`}>{msg.text}</div>}
    </section>
  );
}

function Ops() {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["admin-ops"], queryFn: api.ops, refetchInterval: 15_000 });
  const providers = (q.data?.providers ?? {}) as Record<string, { open: boolean; retry_after: number; recent_failures: number; interval: number }>;
  const queue = (q.data?.queue ?? {}) as { running?: number; queued?: number };
  return (
    <section className="card" aria-labelledby="ops-h">
      <h2 id="ops-h">{t("admin.sec.ops")}</h2>
      <p className="small" style={{ margin: 0 }}>{t("admin.ops.queue", { running: queue.running ?? 0, queued: queue.queued ?? 0 })}</p>
      <div className="tbl-wrap">
        <table className="tbl" style={{ minWidth: 560 }}>
          <thead><tr><th scope="col">{t("admin.ops.provider")}</th><th scope="col" className="l">{t("admin.ops.state")}</th>
            <th scope="col">{t("admin.ops.failures")}</th><th scope="col">{t("admin.ops.interval")}</th></tr></thead>
          <tbody>
            {Object.entries(providers).map(([name, p]) => (
              <tr key={name}>
                <td><strong>{name}</strong></td>
                <td className="l">{p.open ? <span className="tag bad">{t("admin.ops.open", { seconds: Math.ceil(p.retry_after) })}</span>
                  : <span className="tag ok">{t("admin.ops.ok")}</span>}</td>
                <td>{p.recent_failures}</td>
                <td>{t("admin.ops.everyN", { n: p.interval })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <small className="muted small">{t("admin.ops.note")}</small>
    </section>
  );
}
