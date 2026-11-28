import { useI18n } from "../lib/i18n";
import type { RunDetailData } from "./ValuationWaterfallCard";

const DISCOUNT_ORDER = [
  "rf",
  "erp",
  "crp",
  "beta_adopted",
  "beta_ref",
  "ke",
  "kd_pre_tax",
  "tax_rate",
  "kd_after_tax",
  "weight_e",
  "weight_d",
  "wacc",
] as const;

const isRatioKey = (k: string) => !k.startsWith("beta");

export function WaccTerminalCard({ detail }: { detail: RunDetailData }) {
  const { t, f } = useI18n();
  if (!detail.available || (!detail.discount && !detail.terminal)) return null;

  const disc = detail.discount ?? {};
  const val = (k: string): number | null => {
    const v = disc[k]?.value;
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  };

  const rf = val("rf");
  const erp = val("erp");
  const crp = val("crp");
  const beta = val("beta_adopted");
  const ke = val("ke");
  const kdPre = val("kd_pre_tax");
  const tax = val("tax_rate");
  const kdAfter = val("kd_after_tax");
  const we = val("weight_e");
  const wd = val("weight_d");
  const wacc = val("wacc");

  const bd = detail.beta_detail;
  const term = detail.terminal;
  const cfo = detail.cfo_fcff_crosscheck;
  const modelCcy = detail.currency ?? "";

  const fmtPct = (v: number | null | undefined, digits = 2) =>
    v == null || !Number.isFinite(v) ? "—" : f.pct(v, digits, false);
  const fmtNum = (v: number | null | undefined, digits = 2) =>
    v == null || !Number.isFinite(v) ? "—" : v.toFixed(digits);
  const fmtM = (v: number | null | undefined) =>
    v == null || !Number.isFinite(v) ? "—" : `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${modelCcy}m`;

  const wePct = we != null ? Math.max(0, Math.min(100, we * 100)) : null;
  const wdPct = wd != null ? Math.max(0, Math.min(100, wd * 100)) : null;

  const reinvRate =
    term?.g != null && term?.ronic != null && term.ronic > 0 ? term.g / term.ronic : null;

  const auditRows = DISCOUNT_ORDER.filter((k) => disc[k] != null);

  return (
    <section className="card" aria-labelledby="wacc-h" style={{ gap: 16 }}>
      <div>
        <h2 id="wacc-h" style={{ margin: 0 }}>{t("wacc.title")}</h2>
        <span className="muted small">{t("wacc.subtitle")}</span>
      </div>

      {/* 1. CAPM & WACC Glass-Box Equations */}
      {Object.keys(disc).length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="formula-box" aria-label="WACC formulas">
            <div>
              <strong>Ke</strong> = Rf ({fmtPct(rf)}) + βL ({fmtNum(beta, 3)}) × ERP ({fmtPct(erp)}) + CRP ({fmtPct(crp)}) = <strong>{fmtPct(ke)}</strong>
            </div>
            <div>
              <strong>Kd (after-tax)</strong> = Kd ({fmtPct(kdPre)}) × (1 − Tax {fmtPct(tax, 1)}) = <strong>{fmtPct(kdAfter)}</strong>
            </div>
            <div>
              <strong>WACC</strong> = Ke ({fmtPct(ke)}) × E% ({fmtPct(we, 1)}) + Kd ({fmtPct(kdAfter)}) × D% ({fmtPct(wd, 1)}) = <strong style={{ color: "var(--sv-primary)" }}>{fmtPct(wacc)}</strong>
            </div>
          </div>

          {/* Capital structure stacked bar */}
          {wePct != null && wdPct != null && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }} className="small">
                <span><strong>{t("wacc.equityWeight")}</strong> {wePct.toFixed(1)}%</span>
                <span><strong>{t("wacc.debtWeight")}</strong> {wdPct.toFixed(1)}%</span>
              </div>
              <div style={{ display: "flex", height: 10, borderRadius: 6, overflow: "hidden", background: "var(--sv-track)" }}>
                <div style={{ width: `${wePct}%`, background: "var(--sv-primary)" }} title={`${t("wacc.equityWeight")} ${wePct.toFixed(1)}%`} />
                <div style={{ width: `${wdPct}%`, background: "var(--sv-series-4, #eda100)" }} title={`${t("wacc.debtWeight")} ${wdPct.toFixed(1)}%`} />
              </div>
            </div>
          )}

          {/* Beta bottom-up vs regression comparison */}
          {bd && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div className="kv">
                <div>
                  <span className="muted small">{t("wacc.beta.industry")}</span>
                  <strong style={{ fontSize: 15 }}>{bd.industry || "—"}</strong>
                </div>
                <div>
                  <span className="muted small">{t("wacc.beta.unlevered")}</span>
                  <strong>{fmtNum(bd.beta_u_avg, 3)} <span className="muted small">({t("wacc.beta.point")} {fmtNum(bd.beta_u_point, 3)})</span></strong>
                </div>
                <div>
                  <span className="muted small">{t("wacc.beta.relevered")}</span>
                  <strong style={{ color: "var(--sv-primary)" }}>{fmtNum(bd.beta_relevered, 3)} <span className="muted small">(D/E {fmtNum(bd.d_over_e, 2)})</span></strong>
                </div>
                <div>
                  <span className="muted small">{t("wacc.beta.regression")}</span>
                  <strong>{fmtNum(bd.regression_beta, 3)} <span className="muted small">(Blume {fmtNum(bd.blume_beta, 2)})</span></strong>
                </div>
              </div>
              {bd.flag_gap_gt_0_5 && (
                <div className="banner warn" role="note">
                  {t("wacc.beta.gapWarn", {
                    reg: fmtNum(bd.regression_beta, 2),
                    ind: fmtNum(bd.beta_relevered, 2),
                    gap: fmtNum(bd.gap ?? (bd.regression_beta != null && bd.beta_relevered != null ? Math.abs(bd.regression_beta - bd.beta_relevered) : null), 2),
                  })}
                </div>
              )}
            </div>
          )}

          {/* Expandable WACC parameter provenance table */}
          {auditRows.length > 0 && (
            <details className="sv-details">
              <summary className="small">{t("wacc.auditSummary", { n: auditRows.length })}</summary>
              <div tabIndex={0} role="region" aria-label={t("wacc.auditSummary", { n: auditRows.length })} className="tbl-wrap tbl-sticky" style={{ marginTop: 10 }}>
                <table className="tbl" style={{ minWidth: 660 }}>
                  <thead>
                    <tr>
                      <th scope="col" className="l">{t("wacc.col.param")}</th>
                      <th scope="col">{t("wacc.col.value")}</th>
                      <th scope="col" className="l">{t("wacc.col.basis")}</th>
                      <th scope="col">{t("wf.col.date")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditRows.map((k) => {
                      const item = disc[k]!;
                      const srcLabel = item.source_name || item.source;
                      const labelKey = `wacc.param.${k}`;
                      const label = t(labelKey).startsWith("wacc.param.") ? k : t(labelKey);
                      return (
                        <tr key={k}>
                          <th scope="row" className="l">{label}</th>
                          <td>{isRatioKey(k) ? fmtPct(item.value, 2) : fmtNum(item.value, 3)}</td>
                          <td className="l" style={{ whiteSpace: "normal", lineHeight: 1.5 }}>
                            {item.basis || "—"}
                            {item.url && item.url.startsWith("http") && (
                              <> · <a href={item.url} target="_blank" rel="noreferrer">{srcLabel || t("qa.openSource")}</a></>
                            )}
                          </td>
                          <td>{item.data_date || "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </div>
      )}

      {/* 2. Terminal Value Breakdown */}
      {term && (
        <div style={{ borderTop: "1px solid var(--sv-line-faint)", paddingTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
          <h3 style={{ fontSize: "var(--sv-fs-lg)" }}>{t("wacc.tv.title")}</h3>
          <div className="formula-box" aria-label="Terminal value formula">
            <div>
              <strong>TV</strong> = FCFF<sub>N+1</sub> ({fmtM(term.fcff_next)}) ÷ (WACC {fmtPct(wacc)} − g {fmtPct(term.g)}) = <strong>{fmtM(term.terminal_value)}</strong>
            </div>
            <div>
              <strong>PV(TV)</strong> = TV ÷ (1 + WACC)<sup>{fmtNum(term.discount_period_tv, 2)}</sup> = <strong style={{ color: "var(--sv-primary)" }}>{fmtM(term.pv_terminal)}</strong>
              {term.tv_share_of_ev != null ? ` (${t("wf.tvShare", { pct: fmtPct(term.tv_share_of_ev, 1) })})` : ""}
            </div>
            {reinvRate != null && (
              <div>
                <strong>{t("wacc.tv.reinvRate")}</strong> = g ({fmtPct(term.g)}) ÷ RONIC ({fmtPct(term.ronic, 1)}) = <strong>{fmtPct(reinvRate, 1)}</strong>
              </div>
            )}
          </div>
          <dl className="facts" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))" }}>
            {term.g_rationale && (
              <div>
                <dt>{t("wacc.tv.gRationale")}</dt>
                <dd className="small" style={{ fontWeight: 400, lineHeight: 1.55 }}>{term.g_rationale}</dd>
              </div>
            )}
            {term.method_rationale && (
              <div>
                <dt>{t("wacc.tv.methodRationale")}</dt>
                <dd className="small" style={{ fontWeight: 400, lineHeight: 1.55 }}>{term.method_rationale}</dd>
              </div>
            )}
            {term.tv_timing_note && (
              <div>
                <dt>{t("wacc.tv.timingNote")}</dt>
                <dd className="small" style={{ fontWeight: 400, lineHeight: 1.55 }}>{term.tv_timing_note}</dd>
              </div>
            )}
          </dl>
        </div>
      )}

      {/* 3. CFO vs NOPAT-FCFF Accounting Cross-Check */}
      {cfo && (cfo.cfo != null || cfo.nopat != null) && (
        <div style={{ borderTop: "1px solid var(--sv-line-faint)", paddingTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
            <h3 style={{ fontSize: "var(--sv-fs-lg)" }}>
              {t("wacc.cfo.title", { year: cfo.year || "Base" })}
            </h3>
            {cfo.source && cfo.source.startsWith("http") && (
              <a className="small" href={cfo.source} target="_blank" rel="noreferrer">
                {t("qa.openSource")} {cfo.data_date ? `(${cfo.data_date})` : ""}
              </a>
            )}
          </div>
          <div className="kv">
            <div><span className="muted small">{t("wacc.cfo.cfo")}</span><strong>{fmtM(cfo.cfo)}</strong></div>
            <div><span className="muted small">{t("wacc.cfo.capex")}</span><strong>{fmtM(cfo.capex)}</strong></div>
            <div><span className="muted small">{t("wacc.cfo.cfoFcff")}</span><strong style={{ color: "var(--sv-primary)" }}>{fmtM(cfo.fcff_cfo_formula)}</strong></div>
            <div><span className="muted small">{t("wacc.cfo.nopatDa")}</span><strong>{fmtM((cfo.nopat ?? 0) + (cfo.da ?? 0))}</strong></div>
          </div>
          {cfo.note && <p className="muted small" style={{ margin: 0, lineHeight: 1.55 }}>{cfo.note}</p>}
        </div>
      )}
    </section>
  );
}
