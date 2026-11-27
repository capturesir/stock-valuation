import type { StockMethodsOut } from "@sv/api-client";
import { useI18n } from "../lib/i18n";

export function FootballFieldCard({ data, currency }: { data: StockMethodsOut; currency?: string | null }) {
  const { t, f } = useI18n();
  const bars = data.football_field ?? [];
  const warnings = data.warnings ?? [];
  const ccy = data.currency ?? currency;

  if (!bars.length && !data.reverse_dcf) return null;

  const allVals: number[] = [];
  for (const b of bars) {
    allVals.push(b.low, b.mid, b.high);
  }
  if (data.price != null) allVals.push(data.price);
  if (data.composite_value != null) allVals.push(data.composite_value);
  const rawMin = Math.min(...allVals);
  const rawMax = Math.max(...allVals);
  const span = Math.max(rawMax - rawMin, rawMax * 0.1, 1);
  const loAxis = Math.max(0, rawMin - span * 0.12);
  const hiAxis = rawMax + span * 0.12;
  const pct = (v: number) => `${Math.max(0, Math.min(100, ((v - loAxis) / Math.max(hiAxis - loAxis, 1e-6)) * 100)).toFixed(2)}%`;

  return (
    <section className="card" aria-labelledby="ff-heading" style={{ gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div>
          <h2 id="ff-heading" style={{ margin: 0 }}>{t("val.ff.title")}</h2>
          <span className="muted small">{t("val.ff.subtitle")}</span>
        </div>
        {data.composite_value != null && (
          <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            <span className="muted small">{t("stock.composite")}</span>
            <strong style={{ fontSize: 18 }}>{f.money(data.composite_value, ccy)}</strong>
            {data.price != null && (
              <span className="muted small">({t("val.ff.price")}: {f.money(data.price, ccy)})</span>
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

      {bars.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {bars.map((b) => (
            <div key={b.method} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 160px) 1fr auto", gap: 10, alignItems: "center" }}>
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
                {data.price != null && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(data.price),
                      top: -2,
                      bottom: -2,
                      borderLeft: "2px dashed var(--sv-chart-price, #f59e0b)",
                    }}
                    title={`${t("val.ff.price")}: ${f.value(data.price)}`}
                  />
                )}
                {data.composite_value != null && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(data.composite_value),
                      top: -2,
                      bottom: -2,
                      borderLeft: "2px solid var(--sv-pos, #16a34a)",
                    }}
                    title={`${t("stock.composite")}: ${f.value(data.composite_value)}`}
                  />
                )}
              </div>
              <div className="small" style={{ textAlign: "right", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                <strong>{f.value(b.mid)}</strong>
                <span className="muted"> ({f.value(b.low)}–{f.value(b.high)})</span>
              </div>
            </div>
          ))}
          <div className="legend small muted" style={{ marginTop: 4 }}>
            <span><i style={{ width: 12, height: 2, borderTop: "2px dashed var(--sv-chart-price, #f59e0b)" }} />{t("val.ff.price")}</span>
            <span><i style={{ width: 12, height: 2, background: "var(--sv-pos, #16a34a)" }} />{t("stock.composite")}</span>
          </div>
        </div>
      )}

      {data.reverse_dcf && (
        <div style={{ borderTop: "1px solid var(--sv-line-faint)", paddingTop: 10, display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <strong>{t("val.rdcf.title")}</strong>
            <span className="muted small">
              {t("val.rdcf.implied", { g: f.pct(data.reverse_dcf.implied_growth, 1, false), years: data.reverse_dcf.years })}
              {data.reverse_dcf.base_growth != null && ` · ${t("val.rdcf.base", { g: f.pct(data.reverse_dcf.base_growth, 1, false) })}`}
            </span>
          </div>
          <span className={data.reverse_dcf.assessment === "market_more_optimistic" ? "tag warn" : data.reverse_dcf.assessment === "market_more_conservative" ? "tag primary" : "tag neutral"}>
            {t(`val.rdcf.${data.reverse_dcf.assessment}`)}
          </span>
        </div>
      )}
    </section>
  );
}
