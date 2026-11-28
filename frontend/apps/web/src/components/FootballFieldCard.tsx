import type { StockMethodsOut } from "@sv/api-client";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useState, type CSSProperties } from "react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import type { RunDetailData } from "./ValuationWaterfallCard";

type RelBreakdownItem = {
  bear?: number;
  base?: number;
  bull?: number;
  multiples?: number[];
};

const WEIGHTABLE_METHODS = ["dcf", "relative", "ri", "ddm", "pb_roe"] as const;

export function FootballFieldCard({
  data,
  currency,
  ticker,
  consensus,
}: {
  data: StockMethodsOut;
  currency?: string | null;
  ticker?: string;
  consensus?: RunDetailData["ib_compare"];
}) {
  const { t, f } = useI18n();
  const sliderPrefix = useId();
  const [customWeights, setCustomWeights] = useState<Record<string, number> | null>(null);

  useEffect(() => {
    setCustomWeights(null);
  }, [ticker]);

  const tuned = useQuery({
    queryKey: ["methods-tuned", ticker, customWeights],
    queryFn: () => api.methods(ticker!, customWeights ?? undefined),
    enabled: !!ticker && customWeights != null,
    placeholderData: (prev) => prev,
    retry: false,
  });

  const activeData = customWeights && tuned.data ? tuned.data : data;
  const bars = activeData.football_field ?? [];
  const methodsList = activeData.methods ?? [];
  const warnings = activeData.warnings ?? [];
  const ccy = activeData.currency ?? currency;

  const cons = consensus?.consensus;
  const hasConsensus =
    cons != null &&
    (cons.mean != null || cons.median != null) &&
    (cons.low != null || cons.mean != null) &&
    (cons.high != null || cons.mean != null);

  const consLow = hasConsensus ? (cons!.low ?? cons!.median ?? cons!.mean!) : null;
  const consMid = hasConsensus ? (cons!.median ?? cons!.mean!) : null;
  const consHigh = hasConsensus ? (cons!.high ?? cons!.mean ?? cons!.median!) : null;

  const tunableMethods = useMemo(() => {
    const present = new Set((data.methods ?? []).map((m) => m.method));
    return WEIGHTABLE_METHODS.filter((m) => present.has(m));
  }, [data.methods]);

  if (!bars.length && !activeData.reverse_dcf && !hasConsensus) return null;

  const allVals: number[] = [];
  for (const b of bars) {
    allVals.push(b.low, b.mid, b.high);
  }
  if (hasConsensus && consLow != null && consMid != null && consHigh != null) {
    allVals.push(consLow, consMid, consHigh);
  }
  if (activeData.price != null) allVals.push(activeData.price);
  if (activeData.composite_value != null) allVals.push(activeData.composite_value);
  const rawMin = allVals.length ? Math.min(...allVals) : 0;
  const rawMax = allVals.length ? Math.max(...allVals) : 100;
  const span = Math.max(rawMax - rawMin, rawMax * 0.1, 1);
  const loAxis = Math.max(0, rawMin - span * 0.12);
  const hiAxis = rawMax + span * 0.12;
  const pct = (v: number) => `${Math.max(0, Math.min(100, ((v - loAxis) / Math.max(hiAxis - loAxis, 1e-6)) * 100)).toFixed(2)}%`;

  const currentWeights = (m: string): number => {
    if (customWeights && customWeights[m] != null) return customWeights[m];
    const w = data.weights?.[m];
    return typeof w === "number" ? Math.round(w * 100) : 0;
  };

  const handleWeightChange = (method: string, nextVal: number) => {
    setCustomWeights((prev) => {
      const base: Record<string, number> = {};
      for (const m of tunableMethods) {
        base[m] = prev?.[m] ?? Math.round((data.weights?.[m] ?? 0) * 100);
      }
      base[method] = nextVal;
      return base;
    });
  };

  const methodMap = new Map((activeData.methods ?? []).map((m) => [m.method, m]));

  const fmtAssumptionVal = (k: string, v: unknown): string => {
    if (v == null) return "—";
    if (typeof v === "number") {
      if (["wacc", "terminal_g", "ke", "roe", "payout", "g", "growth"].some((s) => k.toLowerCase().includes(s))) {
        return f.pct(v, 2, false);
      }
      return v.toLocaleString(undefined, { maximumFractionDigits: 3 });
    }
    return String(v);
  };

  return (
    <section className="card" aria-labelledby="ff-heading" style={{ gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div>
          <h2 id="ff-heading" style={{ margin: 0 }}>{t("val.ff.title")}</h2>
          <span className="muted small">{t("val.ff.subtitle")}</span>
        </div>
        {activeData.composite_value != null && (
          <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            {customWeights && <span className="tag primary">{t("val.ff.tunedBadge")}</span>}
            <span className="muted small">{t("stock.composite")}</span>
            <strong style={{ fontSize: 18 }}>{f.money(activeData.composite_value, ccy)}</strong>
            {activeData.price != null && (
              <span className="muted small">({t("val.ff.price")}: {f.money(activeData.price, ccy)})</span>
            )}
          </div>
        )}
      </div>

      {warnings.includes("financial_fcff_warning") && (
        <div className="banner warn" role="alert">{t("val.warn.financialFcff")}</div>
      )}
      {warnings.includes("methods_divergence_warning") && (
        <div className="banner info" role="status">{t("val.warn.divergence")}</div>
      )}

      {(bars.length > 0 || hasConsensus) && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {bars.map((b) => (
            <div key={b.method} style={{ display: "grid", gridTemplateColumns: "minmax(130px, 180px) 1fr auto", gap: 10, alignItems: "center" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <strong style={{ fontSize: 13 }}>{t(`val.method.${b.method}`)}</strong>
                <span className="muted small">
                  {b.weight > 0 ? t("stock.weight", { pct: `${Math.round(b.weight * 100)}%` }) : t("val.ff.refOnly")}
                </span>
              </div>
              <div style={{ position: "relative", height: 24, background: "var(--sv-bg-subtle, rgba(127,127,127,0.08))", borderRadius: 4 }}>
                <div
                  style={{
                    position: "absolute",
                    left: pct(b.low),
                    width: `calc(${pct(b.high)} - ${pct(b.low)})`,
                    minWidth: 6,
                    top: 4,
                    bottom: 4,
                    borderRadius: 4,
                    background: b.primary ? "var(--sv-chart-dcf, #2563eb)" : "var(--sv-ink-400, #94a3b8)",
                    opacity: b.primary ? 0.78 : 0.45,
                  }}
                  title={`${f.value(b.low)} – ${f.value(b.high)}`}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(b.mid),
                    top: 2,
                    bottom: 2,
                    width: 3,
                    marginLeft: -1.5,
                    background: "var(--sv-ink-900, #0f172a)",
                    borderRadius: 2,
                  }}
                  title={`${t("stock.scenario.base")}: ${f.value(b.mid)}`}
                />
                {activeData.price != null && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(activeData.price),
                      top: -2,
                      bottom: -2,
                      borderLeft: "2px dashed var(--sv-chart-price, #f59e0b)",
                    }}
                    title={`${t("val.ff.price")}: ${f.value(activeData.price)}`}
                  />
                )}
                {activeData.composite_value != null && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(activeData.composite_value),
                      top: -2,
                      bottom: -2,
                      borderLeft: "2px solid var(--sv-pos, #16a34a)",
                    }}
                    title={`${t("stock.composite")}: ${f.value(activeData.composite_value)}`}
                  />
                )}
              </div>
              <div className="small" style={{ textAlign: "right", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                <strong>{f.value(b.mid)}</strong>
                <span className="muted"> ({f.value(b.low)}–{f.value(b.high)})</span>
              </div>
            </div>
          ))}

          {/* Analyst Consensus Target Band (0% weight reference) */}
          {hasConsensus && consLow != null && consMid != null && consHigh != null && (
            <div style={{ display: "grid", gridTemplateColumns: "minmax(130px, 180px) 1fr auto", gap: 10, alignItems: "center" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <strong style={{ fontSize: 13 }}>
                  {t("val.ff.consensus")}
                  {cons?.url && cons.url.startsWith("http") && (
                    <> <a href={cons.url} target="_blank" rel="noreferrer" className="small" style={{ fontWeight: 400 }}>↗</a></>
                  )}
                </strong>
                <span className="muted small">
                  {cons?.n_analysts ? t("val.ff.consensusCount", { n: cons.n_analysts }) : t("val.ff.refOnly")}
                </span>
              </div>
              <div style={{ position: "relative", height: 24, background: "var(--sv-bg-subtle, rgba(127,127,127,0.08))", borderRadius: 4 }}>
                <div
                  style={{
                    position: "absolute",
                    left: pct(consLow),
                    width: `calc(${pct(consHigh)} - ${pct(consLow)})`,
                    minWidth: 6,
                    top: 5,
                    bottom: 5,
                    borderRadius: 4,
                    background: "var(--sv-series-4, #eda100)",
                    opacity: 0.65,
                  }}
                  title={`${f.value(consLow)} – ${f.value(consHigh)}`}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(consMid),
                    top: 2,
                    bottom: 2,
                    width: 3,
                    marginLeft: -1.5,
                    background: "var(--sv-ink-900, #0f172a)",
                    borderRadius: 2,
                  }}
                  title={`${t("val.ff.consensus")}: ${f.value(consMid)}`}
                />
                {activeData.price != null && (
                  <div
                    style={{ position: "absolute", left: pct(activeData.price), top: -2, bottom: -2, borderLeft: "2px dashed var(--sv-chart-price, #f59e0b)" }}
                  />
                )}
                {activeData.composite_value != null && (
                  <div
                    style={{ position: "absolute", left: pct(activeData.composite_value), top: -2, bottom: -2, borderLeft: "2px solid var(--sv-pos, #16a34a)" }}
                  />
                )}
              </div>
              <div className="small" style={{ textAlign: "right", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                <strong>{f.value(consMid)}</strong>
                <span className="muted"> ({f.value(consLow)}–{f.value(consHigh)})</span>
              </div>
            </div>
          )}

          <div className="legend small muted" style={{ marginTop: 4 }}>
            <span><i style={{ width: 12, height: 2, borderTop: "2px dashed var(--sv-chart-price, #f59e0b)" }} />{t("val.ff.price")}</span>
            <span><i style={{ width: 12, height: 2, background: "var(--sv-pos, #16a34a)" }} />{t("stock.composite")}</span>
            {hasConsensus && (
              <span><i style={{ width: 12, height: 8, background: "var(--sv-series-4, #eda100)", borderRadius: 2, opacity: 0.7 }} />{t("val.ff.consensus")}</span>
            )}
          </div>
        </div>
      )}

      {/* Expandable Formula & Multiple Breakdown per Method */}
      {methodsList.length > 0 && (
        <details className="sv-details">
          <summary className="small">{t("val.ff.breakdownToggle", { n: methodsList.length })}</summary>
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 10 }}>
            {methodsList.map((m) => {
              const assumptions = (m.assumptions ?? {}) as Record<string, unknown>;
              const relBreakdown = m.method === "relative" && assumptions.breakdown && typeof assumptions.breakdown === "object"
                ? (assumptions.breakdown as Record<string, RelBreakdownItem>)
                : null;
              const flatEntries = Object.entries(assumptions).filter(([k, v]) => k !== "breakdown" && v != null && typeof v !== "object");

              return (
                <div
                  key={m.method}
                  style={{
                    background: "var(--sv-card)",
                    border: "1px solid var(--sv-line-soft)",
                    borderRadius: "var(--sv-radius-lg)",
                    padding: "10px 12px",
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                    <strong className="small">{t(`val.method.${m.method}`)}</strong>
                    <span className="small muted">
                      {t("stock.scenario.bear")} {f.value(m.bear)} · <strong>{t("stock.scenario.base")} {f.value(m.base)}</strong> · {t("stock.scenario.bull")} {f.value(m.bull)}
                    </span>
                  </div>
                  {relBreakdown && Object.keys(relBreakdown).length > 0 && (
                    <div tabIndex={0} role="region" aria-label={t(`val.method.${m.method}`)} className="tbl-wrap">
                      <table className="tbl" style={{ minWidth: 520 }}>
                        <thead>
                          <tr>
                            <th scope="col" className="l">{t("val.rel.metric")}</th>
                            <th scope="col">{t("val.rel.multiples")}</th>
                            <th scope="col">{t("stock.scenario.bear")}</th>
                            <th scope="col">{t("stock.scenario.base")}</th>
                            <th scope="col">{t("stock.scenario.bull")}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(relBreakdown).map(([mk, item]) => {
                            const mkKey = `val.rel.m.${mk}`;
                            const mkLabel = t(mkKey).startsWith("val.rel.m.") ? mk.toUpperCase() : t(mkKey);
                            const multStr = Array.isArray(item.multiples) && item.multiples.length === 3
                              ? `${item.multiples[0]}x / ${item.multiples[1]}x / ${item.multiples[2]}x`
                              : "—";
                            return (
                              <tr key={mk}>
                                <th scope="row" className="l">{mkLabel}</th>
                                <td>{multStr}</td>
                                <td>{f.value(item.bear)}</td>
                                <td><strong>{f.value(item.base)}</strong></td>
                                <td>{f.value(item.bull)}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {flatEntries.length > 0 && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px" }} className="small">
                      {flatEntries.map(([k, v]) => (
                        <span key={k}>
                          <span className="muted">{k}: </span>
                          <strong>{fmtAssumptionVal(k, v)}</strong>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </details>
      )}

      {/* Interactive Method Weight Tuner */}
      {ticker && tunableMethods.length >= 2 && (
        <details className="sv-details">
          <summary className="small">{t("val.ff.tunerTitle")}</summary>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span className="muted small">{t("val.ff.tunerHint")}</span>
              {customWeights && (
                <button type="button" className="btn secondary small" onClick={() => setCustomWeights(null)}>
                  {t("val.ff.tunerReset")}
                </button>
              )}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: 12 }}>
              {tunableMethods.map((m) => {
                const rawVal = currentWeights(m);
                const effW = activeData.weights?.[m] ?? 0;
                const inputId = `${sliderPrefix}-${m}`;
                const mv = methodMap.get(m)?.base;
                return (
                  <div key={m} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 6 }}>
                      <label htmlFor={inputId} className="small" style={{ fontWeight: 600 }}>
                        {t(`val.method.${m}`)}
                      </label>
                      <span className="small" style={{ fontVariantNumeric: "tabular-nums" }}>
                        <strong>{Math.round(effW * 100)}%</strong>
                        {mv != null ? <span className="muted"> ({f.value(mv)})</span> : null}
                      </span>
                    </div>
                    <input
                      id={inputId}
                      type="range"
                      className="sv-slider"
                      min={0}
                      max={100}
                      step={5}
                      value={rawVal}
                      aria-valuetext={`${Math.round(effW * 100)}%`}
                      style={{
                        "--attr-min": 0,
                        "--attr-max": 100,
                        "--control-value": rawVal,
                      } as CSSProperties}
                      onChange={(e) => handleWeightChange(m, Number(e.target.value))}
                    />
                  </div>
                );
              })}
            </div>
            {customWeights && data.composite_value != null && activeData.composite_value != null && (
              <div className="banner info small" style={{ justifyContent: "space-between" }}>
                <span>
                  {t("val.ff.tunerCompare", {
                    base: f.money(data.composite_value, ccy),
                    tuned: f.money(activeData.composite_value, ccy),
                    delta: f.pct(activeData.composite_value / data.composite_value - 1, 1, true),
                  })}
                </span>
                {tuned.isFetching && <span className="muted">{t("common.loading")}</span>}
              </div>
            )}
          </div>
        </details>
      )}

      {activeData.reverse_dcf && (
        <div style={{ borderTop: "1px solid var(--sv-line-faint)", paddingTop: 10, display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <strong>{t("val.rdcf.title")}</strong>
            <span className="muted small">
              {t("val.rdcf.implied", { g: f.pct(activeData.reverse_dcf.implied_growth, 1, false), years: activeData.reverse_dcf.years })}
              {activeData.reverse_dcf.base_growth != null && ` · ${t("val.rdcf.base", { g: f.pct(activeData.reverse_dcf.base_growth, 1, false) })}`}
            </span>
          </div>
          <span className={activeData.reverse_dcf.assessment === "market_more_optimistic" ? "tag warn" : activeData.reverse_dcf.assessment === "market_more_conservative" ? "tag primary" : "tag neutral"}>
            {t(`val.rdcf.${activeData.reverse_dcf.assessment}`)}
          </span>
        </div>
      )}
    </section>
  );
}
