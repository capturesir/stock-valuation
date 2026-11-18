import { ApiError, type StockProfile, type Suggestion } from "@sv/api-client";
import { Link } from "react-router-dom";
import { useI18n } from "../lib/i18n";

/** Company facts shown first, so the user can confirm the ticker is the company they meant. */
export function CompanyCard({ p }: { p: StockProfile }) {
  const { t, f } = useI18n();
  const row = (k: string, v: React.ReactNode) => (v == null || v === "" ? null : <div><dt>{t(k)}</dt><dd>{v}</dd></div>);
  const cap = p.market_cap != null ? f.compact(p.market_cap, p.currency) : null;
  return (
    <section className="card company" aria-labelledby="company-h">
      <div className="company-head">
        <h2 id="company-h">{t("profile.title")}</h2>
        <span className="muted small">{t("profile.source", { source: p.source })}</span>
      </div>
      <dl className="facts">
        {row("profile.name", p.name)}
        {row("profile.exchange", p.exchange)}
        {row("profile.currency", p.financial_currency && p.financial_currency !== p.currency
          ? t("profile.currencyBoth", { trade: p.currency ?? "—", report: p.financial_currency }) : p.currency)}
        {row("profile.type", p.quote_type)}
        {row("profile.sector", [p.sector, p.industry].filter(Boolean).join(" · "))}
        {row("profile.country", p.country)}
        {row("profile.price", p.price != null ? `${f.money(p.price, p.currency)}（${p.price_date}）` : null)}
        {row("profile.range52", p.week52_low != null && p.week52_high != null ? `${f.money(p.week52_low, p.currency)} – ${f.money(p.week52_high, p.currency)}` : null)}
        {row("profile.marketCap", cap)}
        {row("profile.website", p.website ? <a href={p.website} target="_blank" rel="noreferrer">{p.website.replace(/^https?:\/\//, "")}</a> : null)}
      </dl>
      {p.summary && <p className="muted company-summary">{p.summary}</p>}
    </section>
  );
}

/** Not found: say so plainly and offer what Yahoo search suggests. */
export function TickerNotFound({ ticker, error }: { ticker: string; error: unknown }) {
  const { t } = useI18n();
  const e = error instanceof ApiError ? error : null;
  const suggestions = ((e?.detail?.suggestions as Suggestion[] | undefined) ?? []);
  return (
    <section className="card" role="alert" aria-labelledby="nf-h">
      <h2 id="nf-h">{t("profile.notFoundTitle", { ticker })}</h2>
      <p style={{ margin: 0 }}>{e?.message ?? String(error)}</p>
      {suggestions.length > 0 && <>
        <span className="field">{t("profile.didYouMean")}</span>
        <ul className="suggest">
          {suggestions.map((s) => (
            <li key={s.ticker}><Link to={`/stock/${encodeURIComponent(s.ticker)}`}><strong>{s.ticker}</strong>
              <span>{[s.name, s.exchange].filter(Boolean).join(" · ")}</span></Link></li>
          ))}
        </ul>
      </>}
    </section>
  );
}
