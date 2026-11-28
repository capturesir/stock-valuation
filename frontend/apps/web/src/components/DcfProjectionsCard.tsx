import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../lib/i18n";
import { useTheme } from "../lib/theme";
import type { RunDetailData } from "./ValuationWaterfallCard";

echarts.use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

type ProjRow = {
  year: string;
  period_length_years?: number;
  revenue_growth?: number | null;
  rev_growth?: number | null;
  revenue?: number | null;
  ebit_margin?: number | null;
  ebit?: number | null;
  tax_rate?: number | null;
  nopat?: number | null;
  da?: number | null;
  capex?: number | null;
  delta_nwc?: number | null;
  dnwc?: number | null;
  fcff?: number | null;
  discount_period?: number | null;
  discount_factor?: number | null;
  pv_fcff?: number | null;
  growth_rationale?: string | null;
  revenue_growth_base?: string | null;
  margin_rationale?: string | null;
  reinvestment_rationale?: string | null;
  delta_nwc_rule?: string | null;
  sources?: string[] | null;
};

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function DcfProjectionsCard({ detail }: { detail: RunDetailData }) {
  const { t, f } = useI18n();
  const themeVersion = useTheme().version;
  const chartEl = useRef<HTMLDivElement>(null);

  const rows = useMemo<ProjRow[]>(
    () => (Array.isArray(detail.projections) ? (detail.projections as unknown as ProjRow[]) : []),
    [detail.projections],
  );
  const [selectedIdx, setSelectedIdx] = useState<number>(0);

  useEffect(() => {
    setSelectedIdx(0);
  }, [detail.run_id]);

  const modelCcy = detail.currency ?? "";
  const fmtM = (v: number | null | undefined) =>
    v == null || !Number.isFinite(v) ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 1 });

  const option = useMemo(() => {
    if (!rows.length) return null;
    const ink = css("--sv-ink-900"), muted = css("--sv-ink-500"), faint = css("--sv-line-faint");
    const card = css("--sv-card"), line = css("--sv-line");
    const cRev = css("--sv-series-1") || "#2a78d6";
    const cFcff = css("--sv-series-3") || "#1baf7a";
    const cMargin = css("--sv-series-2") || "#eb6834";
    const cGrowth = css("--sv-series-4") || "#eda100";

    const labels = rows.map((r) => String(r.year ?? ""));
    const revData = rows.map((r) => (num(r.revenue) != null ? Number(num(r.revenue)!.toFixed(1)) : null));
    const fcffData = rows.map((r) => (num(r.fcff) != null ? Number(num(r.fcff)!.toFixed(1)) : null));
    const marginData = rows.map((r) => (num(r.ebit_margin) != null ? Number((num(r.ebit_margin)! * 100).toFixed(1)) : null));
    const growthData = rows.map((r) => {
      const g = num(r.revenue_growth) ?? num(r.rev_growth);
      return g != null ? Number((g * 100).toFixed(1)) : null;
    });

    return {
      animation: false,
      grid: { left: 10, right: 10, top: 38, bottom: 26, containLabel: true },
      legend: {
        top: 0,
        textStyle: { color: muted, fontSize: 12 },
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: card,
        borderColor: line,
        textStyle: { color: ink, fontSize: 12 },
      },
      xAxis: {
        type: "category",
        data: labels,
        axisLine: { lineStyle: { color: line } },
        axisLabel: { color: muted, interval: 0, rotate: labels.length > 8 ? 22 : 0 },
      },
      yAxis: [
        {
          type: "value",
          name: `${modelCcy}m`,
          nameTextStyle: { color: muted, fontSize: 11 },
          axisLabel: { color: muted },
          splitLine: { lineStyle: { color: faint } },
        },
        {
          type: "value",
          name: "%",
          nameTextStyle: { color: muted, fontSize: 11 },
          axisLabel: { color: muted, formatter: "{value}%" },
          splitLine: { show: false },
        },
      ],
      series: [
        { name: t("proj.row.revenue"), type: "bar", yAxisIndex: 0, data: revData, itemStyle: { color: cRev, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 26 },
        { name: t("proj.row.fcff"), type: "bar", yAxisIndex: 0, data: fcffData, itemStyle: { color: cFcff, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 26 },
        { name: t("proj.row.ebitMargin"), type: "line", yAxisIndex: 1, data: marginData, smooth: true, symbolSize: 6, lineStyle: { color: cMargin, width: 2.2 }, itemStyle: { color: cMargin } },
        { name: t("proj.row.yoy"), type: "line", yAxisIndex: 1, data: growthData, smooth: true, symbolSize: 6, lineStyle: { color: cGrowth, width: 2, type: "dashed" }, itemStyle: { color: cGrowth } },
      ],
    };
  }, [rows, modelCcy, t, themeVersion]);

  useEffect(() => {
    if (!chartEl.current || !option) return;
    const chart = echarts.init(chartEl.current, undefined, { renderer: "svg" });
    chart.setOption(option);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(chartEl.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [option]);

  if (!detail.available || !rows.length) return null;

  const active = rows[Math.min(selectedIdx, rows.length - 1)];
  const activeGrowthNote = active?.growth_rationale || active?.revenue_growth_base;
  const activeReinvNote = [active?.reinvestment_rationale, active?.delta_nwc_rule].filter(Boolean).join(" · ");
  const activeSources = (active?.sources ?? []).filter((s) => typeof s === "string" && s.startsWith("http"));
  const term = detail.terminal;

  const lineItems: {
    key: string;
    label: string;
    bold?: boolean;
    sub?: boolean;
    render: (r: ProjRow) => string;
    terminalVal?: string;
  }[] = [
    { key: "rev", label: t("proj.row.revenue"), bold: true, render: (r) => fmtM(num(r.revenue)), terminalVal: "—" },
    {
      key: "yoy",
      label: t("proj.row.yoy"),
      sub: true,
      render: (r) => {
        const g = num(r.revenue_growth) ?? num(r.rev_growth);
        return g != null ? f.pct(g, 1, true) : "—";
      },
      terminalVal: term?.g != null ? f.pct(term.g, 2, false) : "—",
    },
    { key: "ebit", label: t("proj.row.ebit"), render: (r) => fmtM(num(r.ebit)), terminalVal: "—" },
    {
      key: "ebit_m",
      label: t("proj.row.ebitMargin"),
      sub: true,
      render: (r) => {
        const m = num(r.ebit_margin);
        return m != null ? f.pct(m, 1, false) : "—";
      },
      terminalVal: "—",
    },
    {
      key: "tax",
      label: t("proj.row.taxRate"),
      sub: true,
      render: (r) => {
        const tr = num(r.tax_rate);
        return tr != null ? f.pct(tr, 1, false) : "—";
      },
      terminalVal: "—",
    },
    { key: "nopat", label: t("proj.row.nopat"), bold: true, render: (r) => fmtM(num(r.nopat)), terminalVal: "—" },
    { key: "da", label: t("proj.row.da"), sub: true, render: (r) => fmtM(num(r.da)), terminalVal: "—" },
    { key: "capex", label: t("proj.row.capex"), sub: true, render: (r) => fmtM(num(r.capex)), terminalVal: "—" },
    {
      key: "dnwc",
      label: t("proj.row.dnwc"),
      sub: true,
      render: (r) => fmtM(num(r.delta_nwc) ?? num(r.dnwc)),
      terminalVal: "—",
    },
    {
      key: "fcff",
      label: t("proj.row.fcff"),
      bold: true,
      render: (r) => fmtM(num(r.fcff)),
      terminalVal: fmtM(num(term?.fcff_next)),
    },
    {
      key: "t",
      label: t("proj.row.discountPeriod"),
      sub: true,
      render: (r) => {
        const tp = num(r.discount_period);
        return tp != null ? tp.toFixed(2) : "—";
      },
      terminalVal: num(term?.discount_period_tv) != null ? num(term?.discount_period_tv)!.toFixed(2) : "—",
    },
    {
      key: "df",
      label: t("proj.row.discountFactor"),
      sub: true,
      render: (r) => {
        const df = num(r.discount_factor);
        return df != null ? df.toFixed(4) : "—";
      },
      terminalVal: term?.terminal_value != null ? `TV ${fmtM(num(term.terminal_value))}` : "—",
    },
    {
      key: "pv",
      label: t("proj.row.pvFcff"),
      bold: true,
      render: (r) => fmtM(num(r.pv_fcff)),
      terminalVal: fmtM(num(term?.pv_terminal)),
    },
  ];

  return (
    <section className="card" aria-labelledby="proj-h" style={{ gap: 14 }}>
      <div>
        <h2 id="proj-h" style={{ margin: 0 }}>{t("proj.title")}</h2>
        <span className="muted small">{t("proj.subtitle", { n: rows.length, ccy: modelCcy })}</span>
      </div>

      {/* Dual-axis Revenue/FCFF + Margin/Growth chart */}
      <div ref={chartEl} className="chart" style={{ height: 280 }} role="img" aria-label={t("proj.chartAria")} />

      {/* Interactive 10-year + Terminal table */}
      <div tabIndex={0} role="region" aria-labelledby="proj-h" className="tbl-wrap tbl-sticky">
        <table className="tbl std" style={{ minWidth: 820 }}>
          <thead>
            <tr>
              <th scope="col" className="l">{t("proj.col.item")}</th>
              {rows.map((r, idx) => {
                const isStub = num(r.period_length_years) != null && num(r.period_length_years)! < 0.95;
                const isActive = idx === selectedIdx;
                return (
                  <th key={idx} scope="col">
                    <button
                      type="button"
                      className={`cell${isActive ? " active" : ""}`}
                      onClick={() => setSelectedIdx(idx)}
                      aria-pressed={isActive}
                      title={t("proj.clickYearHint")}
                      style={{ fontWeight: 600 }}
                    >
                      {r.year}
                      {isStub && <span className="tag warn" style={{ marginLeft: 4, fontSize: 10, padding: "1px 5px" }}>{t("proj.stub")}</span>}
                    </button>
                  </th>
                );
              })}
              {term && (
                <th scope="col" className="ttm">{t("proj.col.terminal")}</th>
              )}
            </tr>
          </thead>
          <tbody>
            {lineItems.map((li) => (
              <tr key={li.key} className={li.bold ? "total" : li.sub ? "sub" : undefined}>
                <th scope="row" className="l">{li.label}</th>
                {rows.map((r, idx) => (
                  <td
                    key={idx}
                    onClick={() => setSelectedIdx(idx)}
                    style={{
                      cursor: "pointer",
                      background: idx === selectedIdx ? "var(--sv-primary-weak)" : undefined,
                    }}
                  >
                    {li.render(r)}
                  </td>
                ))}
                {term && <td className="ttm">{li.terminalVal ?? "—"}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Selected year assumption lineage box */}
      {active && (
        <div
          style={{
            background: "var(--sv-sunken)",
            border: "1px solid var(--sv-line-soft)",
            borderRadius: "var(--sv-radius-lg)",
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
          aria-live="polite"
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <strong className="small">{t("proj.lineageTitle", { year: active.year })}</strong>
            <span className="muted small">{t("proj.clickYearHint")}</span>
          </div>
          {activeGrowthNote || active.margin_rationale || activeReinvNote ? (
            <dl className="facts" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))" }}>
              {activeGrowthNote && (
                <div>
                  <dt>{t("proj.lineage.growth")}</dt>
                  <dd className="small" style={{ fontWeight: 400, lineHeight: 1.55 }}>{activeGrowthNote}</dd>
                </div>
              )}
              {active.margin_rationale && (
                <div>
                  <dt>{t("proj.lineage.margin")}</dt>
                  <dd className="small" style={{ fontWeight: 400, lineHeight: 1.55 }}>{active.margin_rationale}</dd>
                </div>
              )}
              {activeReinvNote && (
                <div>
                  <dt>{t("proj.lineage.reinv")}</dt>
                  <dd className="small" style={{ fontWeight: 400, lineHeight: 1.55 }}>{activeReinvNote}</dd>
                </div>
              )}
            </dl>
          ) : (
            <span className="muted small">{t("proj.lineage.none")}</span>
          )}
          {activeSources.length > 0 && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }} className="small">
              <span className="muted">{t("proj.lineage.sources")}：</span>
              {activeSources.map((url, idx) => (
                <a key={idx} href={url} target="_blank" rel="noreferrer">
                  [{idx + 1}] {(() => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return t("qa.openSource"); } })()}
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
