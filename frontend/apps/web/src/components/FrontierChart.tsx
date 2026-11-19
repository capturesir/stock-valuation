import type { OptimizeOut } from "@sv/api-client";
import { LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";
import { useI18n } from "../lib/i18n";
import { useTheme } from "../lib/theme";
import { seriesColor } from "./CompareChart";

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent, SVGRenderer]);
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Risk (annualised volatility) against annualised return: sampled portfolios, the efficient frontier,
 *  the chosen portfolio and each asset alone. One axis pair; identity by label and legend, not colour alone. */
export function FrontierChart({ data }: { data: OptimizeOut }) {
  const { t, f } = useI18n();
  const el = useRef<HTMLDivElement>(null);
  const themeVersion = useTheme().version;
  const option = useMemo(() => {
    const muted = css("--sv-ink-500"), faint = css("--sv-line-faint"), ink = css("--sv-ink-900"), line = css("--sv-line");
    const pct = (v: number) => f.pct(v, 0, false);
    const m = data.metrics as Record<string, number>;
    return {
      animation: false,
      grid: { left: 8, right: 56, top: 28, bottom: 28, containLabel: true },   // room for asset labels at the edges
      tooltip: { trigger: "item", backgroundColor: css("--sv-card"), borderColor: line, textStyle: { color: ink },
                 formatter: (p: { seriesName: string; data: [number, number, string?] }) =>
                   `${p.data[2] ?? p.seriesName}<br/>${t("opt.volatility")} ${f.pct(p.data[0], 1, false)} · ${t("opt.return")} ${f.pct(p.data[1], 1, true)}` },
      xAxis: { type: "value", name: t("opt.volatility"), nameLocation: "middle", nameGap: 22, scale: true,
               axisLabel: { color: muted, formatter: pct }, splitLine: { lineStyle: { color: faint } }, nameTextStyle: { color: muted } },
      yAxis: { type: "value", scale: true, axisLabel: { color: muted, formatter: pct }, splitLine: { lineStyle: { color: faint } } },
      series: [
        { name: t("opt.candidates"), type: "scatter", symbolSize: 4, silent: true, itemStyle: { color: css("--sv-ink-300"), opacity: 0.5 },
          data: data.cloud.map((c) => [c.volatility, c.return]) },
        { name: t("opt.frontier"), type: "line", symbol: "circle", symbolSize: 5, lineStyle: { width: 2, color: seriesColor(0) },
          itemStyle: { color: seriesColor(0) }, data: data.frontier.map((p) => [p.volatility, p.return]) },
        { name: t("opt.assets"), type: "scatter", symbolSize: 9, itemStyle: { color: seriesColor(1), borderColor: css("--sv-card"), borderWidth: 2 },
          label: { show: true, position: "right", color: ink, formatter: (p: { data: [number, number, string] }) => p.data[2] },
          data: data.assets.map((a) => [a.volatility, a.return, a.ticker]) },
        { name: t("opt.best"), type: "scatter", symbol: "diamond", symbolSize: 16, z: 10,
          itemStyle: { color: css("--sv-chart-dcf"), borderColor: css("--sv-card"), borderWidth: 2 },
          label: { show: true, position: "top", color: ink, fontWeight: 600, formatter: () => t("opt.best") },
          data: [[m.volatility, m.return, t("opt.best")]] },
      ],
    };
  }, [data, t, f, themeVersion]);
  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.getInstanceByDom(el.current) ?? echarts.init(el.current, undefined, { renderer: "svg" });
    chart.setOption(option, true);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    return () => ro.disconnect();
  }, [option]);
  useEffect(() => () => { if (el.current) echarts.getInstanceByDom(el.current)?.dispose(); }, []);
  return <div ref={el} style={{ width: "100%", height: 380 }} role="img" aria-label={t("opt.frontierLabel")} />;
}
