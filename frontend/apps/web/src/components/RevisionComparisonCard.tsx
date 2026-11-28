import type { RunOut } from "@sv/api-client";
import { useI18n } from "../lib/i18n";

const dcf = (r: RunOut | null | undefined, s: string) =>
  r?.methods.find((m) => m.method === "dcf" && m.scenario === s)?.value ?? null;

export function RevisionComparisonCard({
  latest,
  prev,
  ticker,
  currency,
}: {
  latest: RunOut;
  prev: RunOut | null | undefined;
  ticker: string;
  currency?: string | null;
}) {
  const { t, f } = useI18n();
  if (!prev) return null;

  const isHk = /\.hk$/i.test(ticker);
  const srcLabel = (r: RunOut) => {
    const raw = r.statement_source === "sec_standard" && isHk ? "hkex_standard" : (r.statement_source ?? "yahoo");
    const base = t(`run.source.${raw}`);
    return r.ruleset_version ? `${base} · ${r.ruleset_version}` : base;
  };

  const prevBase = dcf(prev, "base");
  const curBase = dcf(latest, "base");
  const prevComp = prev.composite_value;
  const curComp = latest.composite_value;

  const fmtPctDelta = (a: number | null | undefined, b: number | null | undefined) => {
    if (a == null || b == null || a === 0) return { text: "—", cls: "" };
    const diff = b - a;
    const pct = b / a - 1;
    return {
      text: `${f.delta(diff)} (${f.pct(pct, 1, true)})`,
      cls: diff > 0 ? "pos" : diff < 0 ? "neg" : "",
    };
  };

  const fmtBpsDelta = (a: number | null | undefined, b: number | null | undefined) => {
    if (a == null || b == null) return { text: "—", cls: "" };
    const bps = Math.round((b - a) * 10000);
    return {
      text: `${bps >= 0 ? "+" : ""}${bps} bps`,
      cls: bps === 0 ? "" : bps < 0 ? "pos" : "neg",
    };
  };

  const priceD = fmtPctDelta(prev.price, latest.price);
  const compD = fmtPctDelta(prevComp, curComp);
  const baseD = fmtPctDelta(prevBase, curBase);
  const waccD = fmtBpsDelta(prev.wacc, latest.wacc);
  const gD = fmtBpsDelta(prev.terminal_g, latest.terminal_g);

  return (
    <section className="card" aria-labelledby="rev-h" style={{ gap: 12 }}>
      <div>
        <h2 id="rev-h" style={{ margin: 0 }}>{t("rev.title")}</h2>
        <span className="muted small">
          {t("rev.subtitle", { from: prev.valuation_date, to: latest.valuation_date })}
        </span>
      </div>

      <div tabIndex={0} role="region" aria-labelledby="rev-h" className="tbl-wrap tbl-sticky">
        <table className="tbl" style={{ minWidth: 620 }}>
          <thead>
            <tr>
              <th scope="col" className="l">{t("rev.col.metric")}</th>
              <th scope="col">{t("rev.col.prev", { date: prev.valuation_date })}</th>
              <th scope="col">{t("rev.col.latest", { date: latest.valuation_date })}</th>
              <th scope="col">{t("rev.col.delta")}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row" className="l">{t("rev.row.price")}</th>
              <td>{f.money(prev.price, currency)}</td>
              <td><strong>{f.money(latest.price, currency)}</strong></td>
              <td className={priceD.cls}>{priceD.text}</td>
            </tr>
            <tr>
              <th scope="row" className="l">{t("stock.composite")}</th>
              <td>{f.money(prevComp, currency)}</td>
              <td><strong style={{ color: "var(--sv-primary)" }}>{f.money(curComp, currency)}</strong></td>
              <td className={compD.cls}>{compD.text}</td>
            </tr>
            <tr>
              <th scope="row" className="l">{t("rev.row.dcfBase")}</th>
              <td>{f.money(prevBase, currency)}</td>
              <td><strong>{f.money(curBase, currency)}</strong></td>
              <td className={baseD.cls}>{baseD.text}</td>
            </tr>
            <tr>
              <th scope="row" className="l">{t("rev.row.dcfRange")}</th>
              <td>{f.value(dcf(prev, "bear"))} – {f.value(dcf(prev, "bull"))}</td>
              <td>{f.value(dcf(latest, "bear"))} – {f.value(dcf(latest, "bull"))}</td>
              <td className="muted">—</td>
            </tr>
            <tr>
              <th scope="row" className="l">{t("stock.wacc")}</th>
              <td>{f.pct(prev.wacc, 2, false)}</td>
              <td><strong>{f.pct(latest.wacc, 2, false)}</strong></td>
              <td className={waccD.cls}>{waccD.text}</td>
            </tr>
            <tr>
              <th scope="row" className="l">{t("stock.g")}</th>
              <td>{f.pct(prev.terminal_g, 2, false)}</td>
              <td><strong>{f.pct(latest.terminal_g, 2, false)}</strong></td>
              <td>{gD.text}</td>
            </tr>
            <tr>
              <th scope="row" className="l">{t("run.statements")}</th>
              <td className="small">{srcLabel(prev)}</td>
              <td className="small">{srcLabel(latest)}</td>
              <td className="muted small">{srcLabel(prev) === srcLabel(latest) ? t("rev.same") : t("rev.changed")}</td>
            </tr>
            <tr>
              <th scope="row" className="l">{t("history.col.mode")}</th>
              <td className="small">{t(`run.mode.${prev.ai_mode}`)}</td>
              <td className="small">{t(`run.mode.${latest.ai_mode}`)}</td>
              <td className="muted small">{prev.ai_mode === latest.ai_mode ? t("rev.same") : t("rev.changed")}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
