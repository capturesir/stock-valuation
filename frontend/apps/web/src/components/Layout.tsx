import { LOCALES, type Locale } from "@sv/i18n";
import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Gear, List, Logo, Search, Trend } from "./Icons";
import { PwaPrompt } from "./PwaPrompt";

export function Layout() {
  const { t, f, locale, setLocale } = useI18n();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const ai = useQuery({ queryKey: ["ai-settings"], queryFn: api.aiSettings });
  const usage = useQuery({ queryKey: ["usage"], queryFn: () => api.usage() });
  const last = sessionLast();

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
          <NavLink to="/" className="brand"><Logo /><span>{t("app.name")}</span></NavLink>
          <nav className="topnav" aria-label={t("nav.main")}>
            <NavLink to="/" end>{t("nav.watchlist")}</NavLink>
            {last && <NavLink to={`/stock/${last}`}>{t("nav.stock")}</NavLink>}
          </nav>
          <form className="search" role="search" onSubmit={go}>
            <Search size={16} />
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("nav.search")}
                   aria-label={t("nav.searchLabel")} autoCapitalize="characters" />
          </form>
          <div className="spacer" />
          <label className="sr-only" htmlFor="lang">{t("nav.language")}</label>
          <select id="lang" className="lang" value={locale} onChange={(e) => setLocale(e.target.value as Locale)}>
            {LOCALES.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
          </select>
          <NavLink to="/settings" className="chip-link">
            <span className={`dot ${ai.data?.configured ? "ok" : ""}`} />
            <strong>{ai.data?.configured ? t("ai.connected", { provider }) : t("ai.notConfigured")}</strong>
            {usage.data && <span className="muted muted-hide">
              {t("ai.month", { spent: f.usd(usage.data.total_usd), budget: f.usd(usage.data.budget_monthly_usd) })}</span>}
          </NavLink>
        </div>
      </header>
      <Outlet />
      <nav className="bottomnav" aria-label={t("nav.main")}>
        <NavLink to="/" end><List size={20} />{t("nav.watchlist")}</NavLink>
        <NavLink to={last ? `/stock/${last}` : "/"}><Trend size={20} />{t("nav.stock")}</NavLink>
        <NavLink to="/settings"><Gear size={20} />{t("nav.settings")}</NavLink>
      </nav>
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
