import { ApiError, type AdminUserIn, type AdminUserUpdate, type UserOut } from "@sv/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
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
