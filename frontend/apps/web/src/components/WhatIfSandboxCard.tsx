import { useEffect, useId, useMemo, useState, type CSSProperties } from "react";
import { useI18n } from "../lib/i18n";
import type { RunDetailData } from "./ValuationWaterfallCard";

type YearParam = {
  rev: number;
  g: number;
  margin: number;
  tax: number;
  netReinvPct: number;
  t: number;
};

type ModelParams = {
  baseRev: number;
  years: YearParam[];
  wacc0: number;
  g0: number;
  ronic: number;
  bridgeTotal: number;
  shares: number;
  fx: number;
  tvT: number;
  price: number | null;
  baseFv: number | null;
};

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function extractModelParams(detail: RunDetailData): ModelParams | null {
  if (!detail.available || !Array.isArray(detail.projections) || !detail.projections.length) return null;
  const wacc0 = num(detail.discount?.wacc?.value);
  const g0 = num(detail.terminal?.g) ?? 0.025;
  const ronic = num(detail.terminal?.ronic);
  const shares = num(detail.shares);
  if (!shares || shares <= 0 || !wacc0 || wacc0 <= 0.01) return null;

  const ev = num(detail.enterprise_value);
  const eq = num(detail.equity_value);
  const bridgeTotal = ev != null && eq != null ? eq - ev : (num(detail.bridge_total) ?? 0);
  const fx = num(detail.fx_if_any?.per_share_fx) ?? num(detail.fx_if_any?.rate) ?? 1;
  const baseRevRaw = num((detail.base_year as Record<string, unknown> | null | undefined)?.revenue);

  const years: YearParam[] = [];
  let prevRev = baseRevRaw;
  detail.projections.forEach((raw, idx) => {
    const p = raw as Record<string, unknown>;
    const rev = num(p.revenue);
    if (rev == null || rev <= 0) return;
    const ebit = num(p.ebit);
    const nopat = num(p.nopat);
    const da = num(p.da) ?? 0;
    const capex = num(p.capex) ?? 0;
    const dnwc = num(p.delta_nwc) ?? num(p.dnwc) ?? 0;
    const fcff = num(p.fcff);
    const t = num(p.discount_period) ?? idx + 0.5;
    const gY = prevRev && prevRev > 0 ? rev / prevRev - 1 : (num(p.revenue_growth) ?? num(p.rev_growth) ?? 0.05);
    const ebitM = ebit != null ? ebit / rev : (num(p.ebit_margin) ?? 0.15);
    const taxRaw = ebit && Math.abs(ebit) > 1e-6 && nopat != null ? 1 - nopat / ebit : (num(p.tax_rate) ?? 0.21);
    const tax = Math.max(0, Math.min(0.45, taxRaw));
    const netReinvPct =
      fcff != null && nopat != null ? (nopat - fcff) / rev : (capex + dnwc - da) / rev;
    years.push({ rev, g: gY, margin: ebitM, tax, netReinvPct, t });
    prevRev = rev;
  });

  if (!years.length) return null;
  const baseRev = baseRevRaw ?? years[0].rev / Math.max(1 + years[0].g, 0.1);
  const tvT = num(detail.terminal?.discount_period_tv) ?? years[years.length - 1].t;

  return {
    baseRev,
    years,
    wacc0,
    g0,
    ronic: ronic && ronic > 0 ? ronic : wacc0,
    bridgeTotal,
    shares,
    fx: fx > 0 ? fx : 1,
    tvT,
    price: num(detail.price),
    baseFv: num(detail.fair_value_per_share),
  };
}

function evalDcf(
  params: ModelParams,
  wacc: number,
  terminalG: number,
  growthShift: number,
  marginShift: number,
): { fv: number; ev: number; pvFcff: number; pvTv: number; tvShare: number } | null {
  if (wacc <= terminalG + 0.005) return null;
  let rev = params.baseRev;
  let pvFcff = 0;
  let lastNopat = 0;
  params.years.forEach((y, idx) => {
    if (growthShift === 0) {
      rev = y.rev;
    } else {
      const w = Math.max(0.25, 1 - idx * 0.08);
      const gAdj = Math.max(-0.5, y.g + growthShift * w);
      rev = rev * (1 + gAdj);
    }
    const mAdj = Math.max(-0.3, Math.min(0.85, y.margin + marginShift));
    const nopat = rev * mAdj * (1 - y.tax);
    const fcff = nopat - rev * y.netReinvPct;
    pvFcff += fcff / Math.pow(1 + wacc, y.t);
    lastNopat = nopat;
  });
  const ronic = Math.max(params.ronic, wacc * 0.85, 0.03);
  const nopatNext = lastNopat * (1 + terminalG);
  const reinvRate = Math.min(0.95, Math.max(0, terminalG / ronic));
  const fcffNext = nopatNext * (1 - reinvRate);
  const tv = fcffNext / (wacc - terminalG);
  const pvTv = tv / Math.pow(1 + wacc, params.tvT);
  const ev = pvFcff + pvTv;
  const eq = ev + params.bridgeTotal;
  const fv = (eq / params.shares) * params.fx;
  if (!Number.isFinite(fv)) return null;
  return { fv, ev, pvFcff, pvTv, tvShare: ev > 0 ? pvTv / ev : 0 };
}

export function WhatIfSandboxCard({ detail }: { detail: RunDetailData }) {
  const { t, f } = useI18n();
  const idPrefix = useId();
  const params = useMemo(() => extractModelParams(detail), [detail]);

  const [wacc, setWacc] = useState<number>(params?.wacc0 ?? 0.1);
  const [termG, setTermG] = useState<number>(params?.g0 ?? 0.025);
  const [growthShift, setGrowthShift] = useState<number>(0);
  const [marginShift, setMarginShift] = useState<number>(0);

  useEffect(() => {
    if (!params) return;
    setWacc(Number(params.wacc0.toFixed(4)));
    setTermG(Number(params.g0.toFixed(4)));
    setGrowthShift(0);
    setMarginShift(0);
  }, [detail.run_id, params]);

  const baseEval = useMemo(
    () => (params ? evalDcf(params, params.wacc0, params.g0, 0, 0) : null),
    [params],
  );
  const sim = useMemo(
    () => (params ? evalDcf(params, wacc, termG, growthShift, marginShift) : null),
    [params, wacc, termG, growthShift, marginShift],
  );

  if (!params || !baseEval) return null;

  const tradeCcy = detail.trading_currency ?? detail.currency ?? "";
  const modelCcy = detail.currency ?? "";
  const refBaseFv = params.baseFv ?? baseEval.fv;
  const isModified =
    Math.abs(wacc - params.wacc0) > 5e-4 ||
    Math.abs(termG - params.g0) > 5e-4 ||
    Math.abs(growthShift) > 1e-4 ||
    Math.abs(marginShift) > 1e-4;

  const waccMin = Math.max(0.04, Number((params.wacc0 - 0.04).toFixed(3)));
  const waccMax = Math.min(0.22, Number((params.wacc0 + 0.04).toFixed(3)));
  const gMin = 0.0;
  const gMax = Math.min(0.06, Math.max(0.03, Number((params.wacc0 - 0.015).toFixed(3))));

  const resetBase = () => {
    setWacc(Number(params.wacc0.toFixed(4)));
    setTermG(Number(params.g0.toFixed(4)));
    setGrowthShift(0);
    setMarginShift(0);
  };

  const applyPreset = (kind: "bear" | "base" | "bull") => {
    if (kind === "base") return resetBase();
    if (kind === "bear") {
      setWacc(Math.min(waccMax, Number((params.wacc0 + 0.01).toFixed(4))));
      setTermG(Math.max(gMin, Number((params.g0 - 0.005).toFixed(4))));
      setGrowthShift(-0.03);
      setMarginShift(-0.02);
    } else {
      setWacc(Math.max(waccMin, Number((params.wacc0 - 0.01).toFixed(4))));
      setTermG(Math.min(gMax, Number((params.g0 + 0.005).toFixed(4))));
      setGrowthShift(0.03);
      setMarginShift(0.02);
    }
  };

  const fvDeltaPct = sim && refBaseFv > 0 ? sim.fv / refBaseFv - 1 : null;
  const priceGap = sim && params.price && params.price > 0 ? sim.fv / params.price - 1 : null;

  const sliders: {
    id: string;
    label: string;
    value: number;
    min: number;
    max: number;
    step: number;
    display: string;
    baseNote: string;
    onChange: (v: number) => void;
  }[] = [
    {
      id: `${idPrefix}-wacc`,
      label: t("stock.wacc"),
      value: wacc,
      min: waccMin,
      max: waccMax,
      step: 0.001,
      display: f.pct(wacc, 2, false),
      baseNote: `${t("stock.scenario.base")} ${f.pct(params.wacc0, 2, false)}`,
      onChange: setWacc,
    },
    {
      id: `${idPrefix}-g`,
      label: t("stock.g"),
      value: termG,
      min: gMin,
      max: gMax,
      step: 0.001,
      display: f.pct(termG, 2, false),
      baseNote: `${t("stock.scenario.base")} ${f.pct(params.g0, 2, false)}`,
      onChange: setTermG,
    },
    {
      id: `${idPrefix}-dg`,
      label: t("whatif.growthShift"),
      value: growthShift,
      min: -0.1,
      max: 0.1,
      step: 0.005,
      display: `${growthShift >= 0 ? "+" : ""}${(growthShift * 100).toFixed(1)}pp`,
      baseNote: t("whatif.growthTaper"),
      onChange: setGrowthShift,
    },
    {
      id: `${idPrefix}-dm`,
      label: t("whatif.marginShift"),
      value: marginShift,
      min: -0.08,
      max: 0.08,
      step: 0.005,
      display: `${marginShift >= 0 ? "+" : ""}${(marginShift * 100).toFixed(1)}pp`,
      baseNote: t("whatif.marginParallel"),
      onChange: setMarginShift,
    },
  ];

  return (
    <section className="card" aria-labelledby="whatif-h" style={{ gap: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div>
          <h2 id="whatif-h" style={{ margin: 0 }}>{t("whatif.title")}</h2>
          <span className="muted small">{t("whatif.subtitle")}</span>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button type="button" className="btn secondary small" onClick={() => applyPreset("bear")}>{t("whatif.preset.bear")}</button>
          <button type="button" className="btn secondary small" onClick={() => applyPreset("base")} disabled={!isModified}>{t("whatif.reset")}</button>
          <button type="button" className="btn secondary small" onClick={() => applyPreset("bull")}>{t("whatif.preset.bull")}</button>
        </div>
      </div>

      {/* Live readout KPI strip */}
      {sim ? (
        <div className="kv" aria-live="polite">
          <div style={{ border: isModified ? "1.5px solid var(--sv-primary)" : undefined, background: isModified ? "var(--sv-primary-weak)" : undefined }}>
            <span className="muted small">{t("whatif.simFv")}</span>
            <strong style={{ fontSize: 22, color: "var(--sv-primary)" }}>{f.money(sim.fv, tradeCcy)}</strong>
            {fvDeltaPct != null && (
              <span className={`small ${fvDeltaPct >= 0 ? "pos" : "neg"}`}>
                {t("whatif.vsBase", { delta: f.pct(fvDeltaPct, 1, true) })}
              </span>
            )}
          </div>
          <div>
            <span className="muted small">{t("whatif.vsPrice")}</span>
            <strong className={priceGap != null ? (priceGap >= 0 ? "pos" : "neg") : undefined}>
              {priceGap != null ? f.pct(priceGap, 1, true) : "—"}
            </strong>
            {params.price != null && (
              <span className="muted small">{t("val.ff.price")} {f.money(params.price, tradeCcy)}</span>
            )}
          </div>
          <div>
            <span className="muted small">{t("wf.ev")}</span>
            <strong>{sim.ev.toLocaleString(undefined, { maximumFractionDigits: 0 })} {modelCcy}m</strong>
            <span className="muted small">{t("wf.pvFcff")} {sim.pvFcff.toLocaleString(undefined, { maximumFractionDigits: 0 })}m</span>
          </div>
          <div>
            <span className="muted small">{t("wf.tvShare", { pct: "" }).trim()}</span>
            <strong>{f.pct(sim.tvShare, 1, false)}</strong>
            <span className="muted small">{t("wf.pvTerminal")} {sim.pvTv.toLocaleString(undefined, { maximumFractionDigits: 0 })}m</span>
          </div>
        </div>
      ) : (
        <div className="banner warn" role="alert">{t("whatif.invalidSpread")}</div>
      )}

      {/* 4 Interactive Sliders */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: 14 }}>
        {sliders.map((s) => (
          <div key={s.id} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
              <label htmlFor={s.id} className="small" style={{ fontWeight: 600 }}>{s.label}</label>
              <strong className="small" style={{ fontVariantNumeric: "tabular-nums", color: "var(--sv-primary)" }}>{s.display}</strong>
            </div>
            <input
              id={s.id}
              type="range"
              className="sv-slider"
              min={s.min}
              max={s.max}
              step={s.step}
              value={s.value}
              aria-valuetext={s.display}
              style={{
                "--attr-min": s.min,
                "--attr-max": s.max,
                "--control-value": s.value,
              } as CSSProperties}
              onChange={(e) => s.onChange(Number(e.target.value))}
            />
            <span className="muted small">{s.baseNote}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
