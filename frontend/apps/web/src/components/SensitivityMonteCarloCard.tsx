import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";

type SensCell = { fair_value: number | null; upside: number | null };
type SensGrid = {
  row_label: string;
  col_label: string;
  rows: number[];
  cols: number[];
  base_row: number;
  base_col: number;
  cells: SensCell[][];
};
type MonteCarloOut = {
  trials: number;
  valid_trials: number;
  mean?: number;
  std?: number;
  p10?: number;
  p25?: number;
  p50?: number;
  p75?: number;
  p90?: number;
  prob_above_price?: number | null;
  prob_margin_of_safety_20?: number | null;
  histogram: { lo: number; hi: number; count: number }[];
};
export type RunSensitivityOut = {
  run_id: string;
  ticker: string;
  available: boolean;
  currency?: string | null;
  price?: number | null;
  base_wacc?: number | null;
  base_terminal_g?: number | null;
  wacc_vs_g?: SensGrid | null;
  wacc_vs_margin?: SensGrid | null;
  monte_carlo?: MonteCarloOut | null;
};

export function SensitivityMonteCarloCard({ runId, currency }: { runId: string; currency?: string | null }) {
  const { t, f } = useI18n();
  const [tab, setTab] = useState<"wacc_vs_g" | "wacc_vs_margin">("wacc_vs_g");
  const q = useQuery({
    queryKey: ["run-sensitivity", runId],
    queryFn: () => api.runSensitivity(runId) as Promise<RunSensitivityOut>,
    enabled: !!runId,
    retry: false,
  });

  const d = q.data;
  if (!d || !d.available) return null;
  const ccy = d.currency ?? currency ?? undefined;
  const grid = tab === "wacc_vs_g" ? d.wacc_vs_g : d.wacc_vs_margin;
  const mc = d.monte_carlo;
  const maxBin = Math.max(1, ...(mc?.histogram ?? []).map((b) => b.count));

  const cellBg = (up: number | null, isBase: boolean) => {
    if (isBase) return "var(--sv-primary-weak)";
    if (up == null) return undefined;
    if (up >= 0.2) return "rgba(27, 175, 122, 0.20)";
    if (up >= 0.0) return "rgba(27, 175, 122, 0.09)";
    if (up <= -0.2) return "rgba(227, 73, 72, 0.18)";
    return "rgba(227, 73, 72, 0.08)";
  };

  return (
    <section className="card" aria-labelledby="sens-mc-h" style={{ gap: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div>
          <h2 id="sens-mc-h" style={{ margin: 0 }}>{t("sens.title")}</h2>
          <span className="muted small">{t("sens.subtitle", { trials: mc?.trials ?? 2000 })}</span>
        </div>
        <div className="seg" role="group" aria-label={t("sens.matrixToggle")}>
          <button type="button" aria-pressed={tab === "wacc_vs_g"} onClick={() => setTab("wacc_vs_g")}>{t("sens.tab.waccG")}</button>
          <button type="button" aria-pressed={tab === "wacc_vs_margin"} onClick={() => setTab("wacc_vs_margin")}>{t("sens.tab.waccMargin")}</button>
        </div>
      </div>

      {/* Monte Carlo KPIs + distribution histogram */}
      {mc && mc.valid_trials > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="kv">
            <div><span className="muted small">{t("sens.mc.p10p90")}</span><strong>{f.money(mc.p10, ccy)} – {f.money(mc.p90, ccy)}</strong></div>
            <div><span className="muted small">{t("sens.mc.median")}</span><strong>{f.money(mc.p50, ccy)}</strong></div>
            <div><span className="muted small">{t("sens.mc.probAbove")}</span><strong>{mc.prob_above_price != null ? f.pct(mc.prob_above_price, 1, false) : "—"}</strong></div>
            <div><span className="muted small">{t("sens.mc.probMos20")}</span><strong>{mc.prob_margin_of_safety_20 != null ? f.pct(mc.prob_margin_of_safety_20, 1, false) : "—"}</strong></div>
          </div>
          {mc.histogram.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="muted small">{t("sens.mc.histTitle", { n: mc.valid_trials.toLocaleString() })}</span>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 84, paddingTop: 6, borderBottom: "1px solid var(--sv-line)" }}>
                {mc.histogram.map((b, i) => {
                  const above = d.price != null && b.lo >= d.price;
                  const hPct = Math.max(4, Math.round((b.count / maxBin) * 100));
                  return (
                    <div
                      key={i}
                      title={`${f.money(b.lo, ccy)} – ${f.money(b.hi, ccy)}: ${b.count}`}
                      style={{
                        flex: 1,
                        height: `${hPct}%`,
                        background: above ? "var(--sv-primary)" : "var(--sv-ink-400)",
                        borderRadius: "3px 3px 0 0",
                        opacity: above ? 0.9 : 0.45,
                      }}
                    />
                  );
                })}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }} className="muted small">
                <span>{f.money(mc.histogram[0].lo, ccy)}</span>
                {d.price != null && <span>{t("sens.mc.priceMark", { price: f.money(d.price, ccy) })}</span>}
                <span>{f.money(mc.histogram[mc.histogram.length - 1].hi, ccy)}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 2D Sensitivity Heatmap */}
      {grid && (
        <div className="tbl-wrap">
          <table className="tbl" style={{ minWidth: 520 }}>
            <thead>
              <tr>
                <th scope="col" className="l">
                  WACC \ {tab === "wacc_vs_g" ? t("stock.g") : t("sens.marginShift")}
                </th>
                {grid.cols.map((c, j) => (
                  <th key={j} scope="col" style={j === grid.base_col ? { fontWeight: 700, color: "var(--sv-primary)" } : undefined}>
                    {tab === "wacc_vs_g" ? f.pct(c, 2, false) : `${c >= 0 ? "+" : ""}${(c * 100).toFixed(1)}pp`}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.rows.map((r, i) => (
                <tr key={i}>
                  <th scope="row" className="l" style={i === grid.base_row ? { fontWeight: 700, color: "var(--sv-primary)" } : undefined}>
                    {f.pct(r, 2, false)}
                  </th>
                  {grid.cells[i].map((cell, j) => {
                    const isBase = i === grid.base_row && j === grid.base_col;
                    return (
                      <td
                        key={j}
                        style={{
                          background: cellBg(cell.upside, isBase),
                          fontWeight: isBase ? 700 : 500,
                          border: isBase ? "1.5px solid var(--sv-primary)" : undefined,
                        }}
                      >
                        {cell.fair_value != null ? (
                          <>
                            <div>{f.value(cell.fair_value)}</div>
                            {cell.upside != null && (
                              <div className="muted small">{f.pct(cell.upside, 0, true)}</div>
                            )}
                          </>
                        ) : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
