import { ApiError, type Prefs } from "@sv/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { savePrefs, useMe } from "../lib/session";

const CURRENCIES = ["USD", "HKD", "CNY", "EUR", "GBP", "JPY", "SGD", "TWD", "AUD", "CAD"];
const REBALANCE = ["none", "monthly", "quarterly", "annual"] as const;
type Msg = { kind: "ok" | "err"; text: string } | null;

export function AccountPage() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const me = useMe().data;
  const [name, setName] = useState("");
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [msg, setMsg] = useState<Record<string, Msg>>({});
  useEffect(() => { if (me) { setName(me.display_name); setPrefs(me.prefs); } }, [me]);

  const fail = (k: string) => (e: unknown) => setMsg((m) => ({ ...m, [k]: { kind: "err", text: e instanceof ApiError ? e.message : String(e) } }));
  const save = useMutation({
    mutationFn: (body: { display_name?: string; prefs?: Prefs; k: string }) => api.updateMe({ display_name: body.display_name, prefs: body.prefs }),
    onSuccess: (u, v) => { qc.setQueryData(["me"], u); setMsg((m) => ({ ...m, [v.k]: { kind: "ok", text: t("common.saved") } })); },
    onError: (e, v) => fail(v.k)(e),
  });
  const changePw = useMutation({
    mutationFn: () => api.changePassword(pw.current, pw.next),
    onSuccess: () => { setPw({ current: "", next: "" }); setMsg((m) => ({ ...m, pw: { kind: "ok", text: t("account.passwordChanged") } })); },
    onError: fail("pw"),
  });
  if (!me || !prefs) return null;
  const Note = ({ k }: { k: string }) => msg[k] ? <div role="status" className={`banner ${msg[k]!.kind === "ok" ? "info" : "err"}`}>{msg[k]!.text}</div> : null;

  return (
    <main className="main">
      <div><h1 style={{ fontSize: "var(--sv-fs-4xl)" }}>{t("account.title")}</h1>
        <p className="muted" style={{ margin: "6px 0 0" }}>{t("account.subtitle")}</p></div>

      <section className="card" aria-labelledby="profile-h">
        <h2 id="profile-h">{t("account.sec.profile")}</h2>
        <div className="kv">
          <div><span className="muted small">{t("auth.email")}</span><strong style={{ fontSize: 15 }}>{me.email}</strong></div>
          <div><span className="muted small">{t("account.role")}</span><strong style={{ fontSize: 15 }}>{t(`account.role.${me.role}`)}</strong>
            {me.role === "admin" && <Link to="/admin" className="small">{t("nav.admin")} →</Link>}</div>
        </div>
        <form className="fields" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate({ display_name: name, k: "profile" }); }}>
          <label className="field">{t("account.displayName")}<input value={name} maxLength={80} required onChange={(e) => setName(e.target.value)} /></label>
          <div style={{ alignSelf: "flex-end" }}><button className="btn primary" type="submit">{t("common.save")}</button></div>
        </form>
        <Note k="profile" />
      </section>

      <section className="card" aria-labelledby="prefs-h">
        <h2 id="prefs-h">{t("account.sec.prefs")}</h2>
        <label className="field">{t("account.displayCurrency")}
          <select value={prefs.display_currency} onChange={(e) => setPrefs({ ...prefs, display_currency: e.target.value })} style={{ maxWidth: 240 }}>
            {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <small>{t("account.displayCurrencyHelp")}</small>
        </label>
        <fieldset className="radios">
          <legend className="field">{t("account.rebalance")}</legend>
          {REBALANCE.map((r) => (
            <label key={r}><input type="radio" name="rebalance" checked={prefs.rebalance === r} onChange={() => setPrefs({ ...prefs, rebalance: r })} />
              {t(`account.rebalance.${r}`)}</label>
          ))}
          <small className="muted small">{t("account.rebalanceHelp")}</small>
        </fieldset>
        <div><button className="btn primary" type="button" onClick={() => savePrefs(qc, { display_currency: prefs.display_currency, rebalance: prefs.rebalance })
          .then(() => setMsg((m) => ({ ...m, prefs: { kind: "ok", text: t("common.saved") } }))).catch(fail("prefs"))}>{t("common.save")}</button></div>
        <Note k="prefs" />
      </section>

      <section className="card" aria-labelledby="pw-h">
        <h2 id="pw-h">{t("account.sec.password")}</h2>
        <form className="fields" onSubmit={(e: FormEvent) => { e.preventDefault(); changePw.mutate(); }}>
          <input type="text" autoComplete="username" value={me.email} readOnly hidden />
          <label className="field">{t("account.currentPassword")}
            <input type="password" autoComplete="current-password" required value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></label>
          <label className="field">{t("account.newPassword")}
            <input type="password" autoComplete="new-password" required minLength={8} value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></label>
          <div style={{ alignSelf: "flex-end" }}><button className="btn primary" type="submit" disabled={changePw.isPending}>{t("account.changePassword")}</button></div>
        </form>
        <Note k="pw" />
      </section>
      <footer className="foot">{t("app.disclaimer")}</footer>
    </main>
  );
}
