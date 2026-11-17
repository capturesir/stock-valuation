import { ApiError } from "@sv/api-client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { BrandMark, useBranding } from "../lib/branding";
import { useI18n } from "../lib/i18n";

export function LoginPage() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const { name } = useBranding();
  const status = useQuery({ queryKey: ["auth-status"], queryFn: api.authStatus });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.login(email.trim(), password);
      qc.setQueryData(["me"], r.user);
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : String(x));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <form className="card login-card" onSubmit={submit} aria-labelledby="login-h">
        <div className="login-brand"><BrandMark size={40} /><span>{name}</span></div>
        <h1 id="login-h">{t("auth.title")}</h1>
        {status.data?.needs_setup && (
          <div className="banner warn" role="status" style={{ flexDirection: "column", alignItems: "flex-start" }}>
            <span>{t("auth.needsSetup")}</span>
            <code className="cmd">python -m backend.app.cli create-user --email you@example.com --role admin</code>
          </div>
        )}
        <label className="field">{t("auth.email")}
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </label>
        <label className="field">{t("auth.password")}
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {err && <div className="banner err" role="alert">{err}</div>}
        <button className="btn primary" type="submit" disabled={busy}>{t("auth.submit")}</button>
        <p className="muted small" style={{ margin: 0 }}>{t("auth.forgot")}</p>
      </form>
      <footer className="foot">{t("app.disclaimer")}</footer>
    </main>
  );
}
