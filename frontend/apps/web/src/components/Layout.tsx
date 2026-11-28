import { LOCALES, type Locale } from "@sv/i18n";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { BrandMark, useBranding } from "../lib/branding";
import { useI18n } from "../lib/i18n";
import { savePrefs, useMe } from "../lib/session";
import { THEMES, useTheme, type Theme } from "../lib/theme";
import { Gear, List, Person, Search, Trend } from "./Icons";
import { BackgroundJobs } from "./BackgroundJobs";
import { PwaPrompt } from "./PwaPrompt";

export function Layout() {
  const { t, f, locale, setLocale } = useI18n();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const ai = useQuery({ queryKey: ["ai-settings"], queryFn: api.aiSettings });
  const usage = useQuery({ queryKey: ["usage"], queryFn: () => api.usage() });
  const last = sessionLast();
  const { name } = useBranding();
  const { theme, setTheme } = useTheme();
  const me = useMe().data;
  const qc = useQueryClient();
  // the account's language wins on sign-in; a change made here is saved to the account
  const synced = useRef<string | null>(null);
  useEffect(() => {
    if (!me || synced.current === me.id) return;
    synced.current = me.id;
    const saved = me.prefs?.locale as Locale | null | undefined;
    if (saved && saved !== locale) setLocale(saved);
    else if (!saved) void savePrefs(qc, { locale }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.id]);
  const changeLocale = (l: Locale) => { setLocale(l); void savePrefs(qc, { locale: l }).catch(() => undefined); };
  const signOut = async () => {
    try { await api.logout(); } finally { qc.clear(); qc.setQueryData(["me"], null); nav("/"); }
  };

  const go = (e: FormEvent) => {
    e.preventDefault();
    const tk = q.trim();
    if (tk) { nav(`/stock/${encodeURIComponent(tk.toUpperCase())}`); setQ(""); }
  };
  const presets = (ai.data?.presets ?? []) as { id: string; label: string }[];
  const provider = presets.find((p) => p.id === ai.data?.provider)?.label ?? ai.data?.provider;

  return (
    <>
      <header className="hdr">
        <div className="hdr-in">
          <NavLink to="/" className="brand"><BrandMark /><span>{name}</span></NavLink>
          <nav className="topnav" aria-label={t("nav.main")}>
            <NavLink to="/" end>{t("nav.watchlist")}</NavLink>
            {last && <NavLink to={`/stock/${last}`}>{t("nav.stock")}</NavLink>}
            <NavLink to="/compare">{t("nav.compare")}</NavLink>
            <NavLink to="/optimize">{t("nav.optimize")}</NavLink>
          </nav>
          <form className="search" role="search" onSubmit={go}>
            <Search size={16} />
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("nav.search")}
                   aria-label={t("nav.searchLabel")} autoCapitalize="characters" />
          </form>
          <div className="spacer" />
          <BackgroundJobs slot="header" />
          <label className="sr-only" htmlFor="lang">{t("nav.language")}</label>
          <select id="lang" className="lang" value={locale} onChange={(e) => changeLocale(e.target.value as Locale)}>
            {LOCALES.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
          </select>
          <label className="sr-only" htmlFor="theme">{t("theme.label")}</label>
          <select id="theme" className="lang" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
            {THEMES.map((x) => <option key={x} value={x}>{t(`theme.${x}`)}</option>)}
          </select>
          <NavLink to="/settings" className="chip-link">
            <span className={`dot ${ai.data?.configured ? "ok" : ""}`} />
            <strong>{ai.data?.configured ? t("ai.connected", { provider }) : t("ai.notConfigured")}</strong>
            {usage.data && <span className="muted muted-hide">
              {t("ai.month", { spent: f.usd(usage.data.total_usd), budget: f.usd(usage.data.budget_monthly_usd) })}</span>}
          </NavLink>
          {me && (
            <div className="usermenu">
              {me.role === "admin" && <NavLink to="/admin" className="muted-hide">{t("nav.admin")}</NavLink>}
              <NavLink to="/account" title={me.email}><Person size={16} /><span className="muted-hide">{me.display_name}</span></NavLink>
              <button type="button" className="linkish" onClick={signOut}>{t("nav.signOut")}</button>
            </div>
          )}
        </div>
      </header>
      <Outlet />
      <nav className="bottomnav" aria-label={t("nav.main")}>
        <NavLink to="/" end><List size={20} />{t("nav.watchlist")}</NavLink>
        <NavLink to={last ? `/stock/${last}` : "/"}><Trend size={20} />{t("nav.stock")}</NavLink>
        <NavLink to="/compare"><List size={20} />{t("nav.compare")}</NavLink>
        <NavLink to="/settings"><Gear size={20} />{t("nav.settings")}</NavLink>
        <NavLink to="/account"><Person size={20} />{t("nav.account")}</NavLink>
      </nav>
      <BackgroundJobs slot="dock" />
      <PwaPrompt />
    </>
  );
}

/** The last stock opened, for the 個股 tab. */
export function sessionLast(): string | null {
  try { return sessionStorage.getItem("sv.lastTicker"); } catch { return null; }
}
export function rememberTicker(t: string) {
  try { sessionStorage.setItem("sv.lastTicker", t); } catch { /* ignore */ }
}
