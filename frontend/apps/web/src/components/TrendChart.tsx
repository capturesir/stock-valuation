import type { PriceSeries, RunOut } from "@sv/api-client";
import { LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";
import { useI18n } from "../lib/i18n";
import { useTheme } from "../lib/theme";

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent, SVGRenderer]);

export type Range = "near5" | "year" | "all";
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const dcf = (r: RunOut, s: string) => r.methods.find((m) => m.method === "dcf" && m.scenario === s)?.value ?? null;

/**
 * Valuation trend (design system: 走勢圖規則): daily price line, DCF base as a step line that
 * holds until the next valuation, bear–bull band, and a marker for each valuation.
 */
export function TrendChart({ runs, prices, range, currency }: { runs: RunOut[]; prices?: PriceSeries; range: Range; currency?: string | null }) {
  const { t, f } = useI18n();
  const el = useRef<HTMLDivElement>(null);
  const themeVersion = useTheme().version;   // colours are read from CSS variables: recompute on theme change
  const done = useMemo(() => runs.filter((r) => r.status === "done" && dcf(r, "base") != null), [runs]);

  const option = useMemo(() => {
    const now = Date.now();
    const at = (r: RunOut) => new Date(r.run_at).getTime();
    let from = 0;
    if (range === "year") from = now - 365 * 86400e3;
    if (range === "near5" && done.length > 5) from = at(done[done.length - 5]);
    const shown = done.filter((r) => at(r) >= from);
    const tail = (v: number | null) => (shown.length ? [[now, v]] : []);
    const ref = shown[shown.length - 1];
    const base = shown.map((r) => [at(r), dcf(r, "base")]).concat(tail(ref ? dcf(ref, "base") : null));
    const bear = shown.map((r) => [at(r), dcf(r, "bear") ?? dcf(r, "base")]);
    const bull = shown.map((r) => [at(r), dcf(r, "bull") ?? dcf(r, "base")]);
    const lastR = shown[shown.length - 1];
    if (lastR) { bear.push([now, bear[bear.length - 1][1]]); bull.push([now, bull[bull.length - 1][1]]); }
    const spread = bull.map(([x, v], i) => [x, (v ?? 0) - (bear[i][1] ?? 0)]);
    const daily = (prices?.points ?? []).map((p) => [new Date(p.date + "T12:00:00").getTime(), p.close])
      .filter(([x]) => x >= from);
    const priceLine = daily.length ? daily : shown.map((r) => [at(r), r.price]);
    const ink = css("--sv-ink-900"), muted = css("--sv-ink-500"), faint = css("--sv-line-faint");
    const blue = css("--sv-chart-dcf"), band = css("--sv-chart-band"), card = css("--sv-card");
    const money = (v: number) => f.money(v, currency);

    return {
      animation: false,
      grid: { left: 8, right: 16, top: 16, bottom: 28, containLabel: true },
      tooltip: {
        trigger: "axis", backgroundColor: card, borderColor: css("--sv-line"), textStyle: { color: ink },
        valueFormatter: (v: number) => (typeof v === "number" ? money(v) : "—"),
      },
      xAxis: { type: "time", axisLine: { lineStyle: { color: css("--sv-line") } }, axisLabel: { color: muted, hideOverlap: true }, splitLine: { show: false } },
      yAxis: { type: "value", scale: true, axisLabel: { color: muted }, splitLine: { lineStyle: { color: faint } } },
      series: [
        { name: "bear", type: "line", step: "end", data: bear, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, tooltip: { show: false } },
        { name: t("stock.trend.band"), type: "line", step: "end", data: spread, stack: "band", symbol: "none",
          lineStyle: { opacity: 0 }, areaStyle: { color: band, opacity: 0.75 }, tooltip: { show: false } },
        { name: t("stock.trend.price"), type: "line", data: priceLine, symbol: "none", lineStyle: { color: ink, width: 1.8 }, z: 3 },
        { name: t("stock.trend.dcf"), type: "line", step: "end", data: base, symbol: "none", lineStyle: { color: blue, width: 3 }, z: 4 },
        { name: t("stock.trend.events"), type: "scatter", data: shown.map((r) => [at(r), dcf(r, "base")]), symbolSize: 10,
          itemStyle: { color: blue, borderColor: card, borderWidth: 2 }, z: 5, tooltip: { show: false } },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, prices, range, currency, t, f, themeVersion]);

  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.init(el.current, undefined, { renderer: "svg" });
    chart.setOption(option);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [option]);

  const latest = done[done.length - 1];
  return (
    <div ref={el} className="chart" role="img"
         aria-label={t("stock.trend.aria", { n: done.length, fair: f.money(latest ? dcf(latest, "base") : null, currency), price: f.money(latest?.price, currency) })} />
  );
}
