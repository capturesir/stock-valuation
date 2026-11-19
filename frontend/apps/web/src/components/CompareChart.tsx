import type { CompareOut } from "@sv/api-client";
import { LineChart } from "echarts/charts";
import { GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";
import { useI18n } from "../lib/i18n";
import { useTheme } from "../lib/theme";

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, SVGRenderer]);

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
/** categorical slot by line position: fixed order; lines 9–10 reuse slots 1–2 with a dashed stroke */
export const seriesColor = (i: number) => css(`--sv-series-${(i % 8) + 1}`);
export const seriesDashed = (i: number) => i >= 8;

/**
 * Comparison chart (roadmap §5.4): every line rebased to 100 (or as a % return), the selected line shaded
 * against the benchmark (blue above, orange below — design tokens chart-up / chart-down), and a separate
 * relative-strength panel (selected ÷ benchmark) — never a second y-axis on the same plot.
 */
export function CompareChart({ data, mode, selected }: { data: CompareOut; mode: "index" | "pct"; selected: string | null }) {
  const { t, f } = useI18n();
  const el = useRef<HTMLDivElement>(null);
  const themeVersion = useTheme().version;

  const option = useMemo(() => {
    const x = data.dates.map((d) => new Date(d + "T12:00:00").getTime());
    const show = (v: number) => (mode === "index" ? v : v / 100 - 1);
    const fmt = (v: number) => (mode === "index" ? v.toFixed(1) : f.pct(v, 1, true));
    const ink = css("--sv-ink-900"), muted = css("--sv-ink-500"), faint = css("--sv-line-faint"), line = css("--sv-line");
    const bench = data.lines.find((l) => l.key === data.benchmark)!;
    const sel = selected && selected !== data.benchmark ? data.lines.find((l) => l.key === selected) : null;
    const series: object[] = data.lines.map((l, i) => ({
      name: `${l.ref}${l.key === data.benchmark ? ` (${t("cmp.benchmark")})` : ""}`, type: "line", symbol: "none",
      data: l.values.map((v, j) => [x[j], show(v)]), xAxisIndex: 0, yAxisIndex: 0, z: l.key === selected ? 5 : 3,
      lineStyle: { width: l.key === selected || l.key === data.benchmark ? 2.5 : 1.5, color: seriesColor(i),
                   type: seriesDashed(i) ? "dashed" : "solid" },
      itemStyle: { color: seriesColor(i) },
    }));
    if (sel) {
      // shading between the selected line and the benchmark: two invisible bases with stacked differences
      const a = sel.values.map(show), b = bench.values.map(show);
      const band = (base: number[], diff: number[], stack: string, color: string) => [
        { type: "line", stack, symbol: "none", silent: true, lineStyle: { opacity: 0 }, tooltip: { show: false },
          data: base.map((v, j) => [x[j], v]), z: 1 },
        { type: "line", stack, symbol: "none", silent: true, lineStyle: { opacity: 0 }, tooltip: { show: false },
          areaStyle: { color, opacity: 0.22 }, data: diff.map((v, j) => [x[j], v]), z: 1 },
      ];
      series.push(...band(b, a.map((v, j) => Math.max(v - b[j], 0)), "win", css("--sv-chart-up")));
      series.push(...band(a, b.map((v, j) => Math.max(v - a[j], 0)), "lose", css("--sv-chart-down")));
      const rel = data.relative.find((r) => r.key === sel.key);
      const idx = data.lines.indexOf(sel);
      if (rel) series.push({ name: t("cmp.relativeOf", { ref: sel.ref }), type: "line", symbol: "none", xAxisIndex: 1, yAxisIndex: 1,
                             data: rel.values.map((v, j) => [x[j], v]), lineStyle: { width: 2, color: seriesColor(idx) },
                             markLine: { symbol: "none", silent: true, lineStyle: { color: muted, type: "dashed" }, label: { show: false },
                                         data: [{ yAxis: 100 }] } });
    }
    // the shaded areas fill from zero: fix the value axis to the lines' own range, or it would start at 0
    const all = data.lines.flatMap((l) => l.values.map(show));
    const lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.04 || 1;
    const axis = (i: number) => ({ type: "time", gridIndex: i, axisLine: { lineStyle: { color: line } },
                                   axisLabel: { color: muted, hideOverlap: true, show: i === 1 || !sel }, splitLine: { show: false } });
    return {
      animation: false,
      grid: sel ? [{ left: 8, right: 16, top: 12, height: "58%", containLabel: true }, { left: 8, right: 16, top: "70%", bottom: 24, containLabel: true }]
        : [{ left: 8, right: 16, top: 12, bottom: 24, containLabel: true }],
      tooltip: { trigger: "axis", backgroundColor: css("--sv-card"), borderColor: line, textStyle: { color: ink },
                 valueFormatter: (v: number) => (typeof v === "number" ? fmt(v) : "—") },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      xAxis: sel ? [axis(0), axis(1)] : [axis(0)],
      yAxis: (sel ? [0, 1] : [0]).map((i) => ({
        type: "value", scale: true, gridIndex: i, splitNumber: i === 1 ? 2 : 5,
        min: i === 0 ? lo - pad : undefined, max: i === 0 ? hi + pad : undefined,
        axisLabel: { color: muted, formatter: i === 0 ? (v: number) => fmt(v) : (v: number) => v.toFixed(0),
                     showMinLabel: i !== 0, showMaxLabel: i !== 0 },
        splitLine: { lineStyle: { color: faint } }, name: i === 1 ? t("cmp.relative") : undefined, nameTextStyle: { color: muted, align: "left" } })),
      series,
    };
  }, [data, mode, selected, t, f, themeVersion]);

  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.getInstanceByDom(el.current) ?? echarts.init(el.current, undefined, { renderer: "svg" });
    chart.setOption(option, true);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    return () => ro.disconnect();
  }, [option]);
  useEffect(() => () => { if (el.current) echarts.getInstanceByDom(el.current)?.dispose(); }, []);

  return <div ref={el} style={{ width: "100%", height: selected && selected !== data.benchmark ? 500 : 360 }}
              role="img" aria-label={t("cmp.chartLabel", { n: data.lines.length })} />;
}
