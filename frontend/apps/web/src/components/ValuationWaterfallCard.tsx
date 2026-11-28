import { useI18n } from "../lib/i18n";

export type BridgeItem = {
  item: string;
  value: number;
  sign: "+" | "-";
  basis?: string;
  source_name?: string;
  url?: string;
  data_date?: string;
};

export type RunDetailData = {
  run_id: string;
  ticker: string;
  available: boolean;
  valuation_date?: string | null;
  currency?: string | null;
  trading_currency?: string | null;
  price?: number | null;
  price_date?: string | null;
  fair_value_per_share?: number | null;
  composite_value?: number | null;
  sum_pv_fcff?: number | null;
  pv_terminal?: number | null;
  enterprise_value?: number | null;
  bridge_total?: number | null;
  equity_value?: number | null;
  shares?: number | null;
  shares_source?: { basis?: string; source?: string; url?: string; data_date?: string } | null;
  fx_if_any?: { pair?: string; rate?: number; per_share_fx?: number; applied?: string } | null;
  discount?: Record<string, { value?: number; basis?: string; source?: string; source_name?: string; url?: string; data_date?: string }> | null;
  beta_detail?: {
    industry?: string;
    beta_u_avg?: number;
    beta_u_point?: number;
    d_over_e?: number;
    beta_relevered?: number;
    regression_beta?: number | null;
    blume_beta?: number | null;
    flag_gap_gt_0_5?: boolean;
    gap?: number | null;
  } | null;
  terminal?: {
    method?: string;
    g?: number;
    ronic?: number;
    discount_period_tv?: number;
    fcff_next?: number;
    terminal_value?: number;
    pv_terminal?: number;
    tv_share_of_ev?: number;
    tv_timing_note?: string;
    g_rationale?: string;
    method_rationale?: string;
  } | null;
  bridge?: BridgeItem[];
  base_year?: Record<string, unknown> | null;
  projections?: Record<string, unknown>[];
  cfo_fcff_crosscheck?: {
    year?: string;
    cfo?: number;
    interest?: number;
    tax?: number;
    capex?: number;
    fcff_cfo_formula?: number;
    fcff_cfo_note?: string;
    nopat?: number;
    da?: number;
    note?: string;
    source?: string;
    data_date?: string;
  } | null;
  ib_compare?: {
    consensus?: {
      source?: string;
      url?: string;
      data_date?: string;
      mean?: number | null;
      median?: number | null;
      low?: number | null;
      high?: number | null;
      n_analysts?: number | null;
      currency?: string | null;
    };
  } | null;
  previous_run?: {
    id: string;
    valuation_date: string;
    run_at?: string | null;
    price?: number | null;
    composite_value?: number | null;
    dcf_base?: number | null;
    wacc?: number | null;
    terminal_g?: number | null;
    statement_source?: string | null;
    ruleset_version?: string | null;
    adjustment_plan?: { kind?: string; version?: number; label?: string } | null;
  } | null;
};

export function ValuationWaterfallCard({ detail }: { detail: RunDetailData }) {
  const { t, f } = useI18n();
  if (!detail.available || detail.enterprise_value == null || detail.equity_value == null) return null;

  const modelCcy = detail.currency ?? "";
  const tradeCcy = detail.trading_currency ?? modelCcy;
  const pvFcff = detail.sum_pv_fcff ?? 0;
  const pvTv = detail.pv_terminal ?? 0;
  const ev = detail.enterprise_value;
  const eq = detail.equity_value;
  const bridge = detail.bridge ?? [];
  const tvShare = detail.terminal?.tv_share_of_ev ?? (ev > 0 ? pvTv / ev : null);
  const maxAbs = Math.max(Math.abs(ev), Math.abs(eq), Math.abs(pvFcff), Math.abs(pvTv), ...bridge.map((b) => Math.abs(b.value)), 1);

  const fmtM = (v: number) => `${v >= 0 ? "" : "−"}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${modelCcy}m`;
  const barW = (v: number) => `${Math.max(2, Math.min(100, (Math.abs(v) / maxAbs) * 100)).toFixed(1)}%`;

  const fxRate = detail.fx_if_any?.per_share_fx ?? detail.fx_if_any?.rate ?? 1;
  const hasFx = Math.abs(fxRate - 1) > 1e-4 && detail.fx_if_any?.pair;

  return (
    <section className="card" aria-labelledby="wf-h" style={{ gap: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div>
          <h2 id="wf-h" style={{ margin: 0 }}>{t("wf.title")}</h2>
          <span className="muted small">{t("wf.subtitle", { ccy: modelCcy })}</span>
        </div>
        {tvShare != null && (
          <span className={`tag ${tvShare > 0.75 ? "warn" : "neutral"}`}>
            {t("wf.tvShare", { pct: f.pct(tvShare, 1, false) })}
          </span>
        )}
      </div>

      {tvShare != null && tvShare > 0.75 && (
        <div className="banner warn" role="note">{t("wf.tvHighWarn", { pct: f.pct(tvShare, 1, false) })}</div>
      )}

      {/* Visual waterfall bars */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {[
          { label: t("wf.pvFcff"), val: pvFcff, color: "var(--sv-primary)", bold: false, sign: "" },
          { label: t("wf.pvTerminal"), val: pvTv, color: "var(--sv-series-4, #eda100)", bold: false, sign: "+" },
          { label: t("wf.ev"), val: ev, color: "var(--sv-ink-900)", bold: true, sign: "=" },
          ...bridge.map((b) => ({
            label: b.item,
            val: b.sign === "-" ? -Math.abs(b.value) : Math.abs(b.value),
            color: b.sign === "+" ? "var(--sv-success)" : "var(--sv-danger)",
            bold: false,
            sign: b.sign,
          })),
          { label: t("wf.equity"), val: eq, color: "var(--sv-primary-strong)", bold: true, sign: "=" },
        ].map((row, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "minmax(150px, 240px) 1fr minmax(120px, auto)", gap: 10, alignItems: "center" }}>
            <span className="small" style={{ fontWeight: row.bold ? 700 : 500 }}>
              {row.sign ? <span className="muted" style={{ marginRight: 4 }}>{row.sign}</span> : null}
              {row.label}
            </span>
            <div className="bar" style={{ height: row.bold ? 12 : 9 }}>
              <i style={{ width: barW(row.val), background: row.color }} />
            </div>
            <span className="small" style={{ textAlign: "right", fontWeight: row.bold ? 700 : 500, fontVariantNumeric: "tabular-nums" }}>
              {fmtM(row.val)}
            </span>
          </div>
        ))}
      </div>

      {/* Per-share conversion equation */}
      <div className="banner info" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <span>
          <strong>{t("wf.perShareFormula")}：</strong>
          {fmtM(eq)} ÷ {detail.shares ? `${detail.shares.toLocaleString(undefined, { maximumFractionDigits: 2 })}m ${t("wf.sharesUnit")}` : "—"}
          {hasFx ? ` × ${fxRate.toFixed(4)} (${detail.fx_if_any?.pair})` : ""}
          {" ＝ "}
          <strong>{f.money(detail.fair_value_per_share, tradeCcy)}</strong>
        </span>
        {detail.shares_source?.basis && (
          <span className="muted small">{detail.shares_source.basis}</span>
        )}
      </div>

      {/* Auditable bridge provenance table */}
      {bridge.length > 0 && (
        <details className="sv-details">
          <summary className="small">
            {t("wf.bridgeAudit", { n: bridge.length })}
          </summary>
          <div tabIndex={0} role="region" aria-label={t("wf.bridgeAudit", { n: bridge.length })} className="tbl-wrap tbl-sticky" style={{ marginTop: 10 }}>
            <table className="tbl" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th scope="col" className="l">{t("wf.col.item")}</th>
                  <th scope="col">{t("wf.col.amount")}</th>
                  <th scope="col" className="l">{t("wf.col.basis")}</th>
                  <th scope="col">{t("wf.col.date")}</th>
                </tr>
              </thead>
              <tbody>
                {bridge.map((b, idx) => (
                  <tr key={idx}>
                    <th scope="row" className="l">
                      <span className={`tag ${b.sign === "+" ? "ok" : "bad"}`} style={{ marginRight: 6 }}>{b.sign}</span>
                      {b.item}
                    </th>
                    <td>{fmtM(b.sign === "-" ? -Math.abs(b.value) : Math.abs(b.value))}</td>
                    <td className="l" style={{ whiteSpace: "normal", lineHeight: 1.5 }}>
                      {b.basis || "—"}
                      {b.url && b.url.startsWith("http") && (
                        <> · <a href={b.url} target="_blank" rel="noreferrer">{b.source_name || t("qa.openSource")}</a></>
                      )}
                    </td>
                    <td>{b.data_date || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}
