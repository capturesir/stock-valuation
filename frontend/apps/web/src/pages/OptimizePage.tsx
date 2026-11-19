import { ApiError, type OptimizeIn, type OptimizeOut } from "@sv/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FrontierChart } from "../components/FrontierChart";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useMe } from "../lib/session";

type Asset = { ticker: string; min: number; max: number };
type Obj = "sharpe" | "max_drawdown" | "return" | "mix";
const RANGES = ["1y", "2y", "3y", "5y", "10y", "ytd", "max"] as const;
const CURRENCIES = ["USD", "HKD", "CNY", "EUR", "GBP", "JPY"];
const KEYS = ["return", "volatility", "sharpe", "max_drawdown"] as const;

/** Portfolio optimizer (roadmap §5.3). */
export function OptimizePage() {
  const { t, f } = useI18n();
  const me = useMe().data;
  const qc = useQueryClient();
  const [assets, setAssets] = useState<Asset[]>([{ ticker: "SPY", min: 0, max: 100 }, { ticker: "QQQ", min: 0, max: 100 },
                                                 { ticker: "TLT", min: 0, max: 100 }]);
  const [obj, setObj] = useState<Obj>("sharpe");
  const [mix, setMix] = useState({ sharpe: 2, max_drawdown: 1, return: 1 });
  const [range, setRange] = useState<OptimizeIn["range"]>("5y");
  const [rebalance, setRebalance] = useState<OptimizeIn["rebalance"]>("none");
  const [currency, setCurrency] = useState("");
  const [rf, setRf] = useState("4");
  const [samples, setSamples] = useState(20000);
  const [seed, setSeed] = useState(42);
  const [oos, setOos] = useState(0.3);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (!me) return;
    if (!currency) setCurrency(me.prefs?.display_currency || "USD");
  }, [me, currency]);
  useEffect(() => { if (me?.prefs?.rebalance) setRebalance(me.prefs.rebalance as OptimizeIn["rebalance"]); }, [me?.prefs?.rebalance]);

  const body = (): OptimizeIn => ({
    assets: assets.filter((a) => a.ticker.trim()).map((a) => ({ ticker: a.ticker.trim().toUpperCase(), min: a.min, max: a.max })),
    objective: obj === "mix" ? mix : { [obj]: 1 }, range, rebalance, currency: currency || undefined,
    rf: Number(rf) / 100 || 0, samples, seed, oos_fraction: oos,
  });
  const run = useMutation({ mutationFn: () => api.optimize(body()), onMutate: () => setMsg(null) });
  const r: OptimizeOut | undefined = run.data;
  const savePf = useMutation({
    mutationFn: () => api.savePortfolio({ name: name.trim(), rebalance: r!.rebalance as OptimizeIn["rebalance"],
                                          holdings: Object.entries(r!.weights).filter(([, w]) => w > 0).map(([ticker, weight]) => ({ ticker, weight })) }),
    onSuccess: (p) => { setMsg(t("opt.savedPortfolio", { name: p.name })); void qc.invalidateQueries({ queryKey: ["portfolios"] }); },
    onError: (e) => setMsg((e as Error).message),
  });
  const set = (i: number, patch: Partial<Asset>) => setAssets(assets.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  const pct = (v: number | null | undefined, signed = false) => (v == null ? "—" : f.pct(v, 1, signed));
  const metric = (k: string, v: number | null | undefined) => (k === "sharpe" ? (v == null ? "—" : v.toFixed(2)) : pct(v, k === "return"));
  const win = r?.window as { start: string; end: string; days: number; limited_by?: string | null } | undefined;
  const o = r?.out_of_sample as undefined | { in_sample: { start: string; end: string; metrics: Record<string, number> };
                                              out_of_sample: { start: string; end: string; metrics: Record<string, number>; equal_weight: Record<string, number> } };

  return (
    <main className="main">
      <div><h1 style={{ fontSize: "var(--sv-fs-4xl)" }}>{t("opt.title")}</h1>
        <p className="muted" style={{ margin: "6px 0 0" }}>{t("opt.subtitle")}</p></div>

      <section className="card" aria-labelledby="opt-in" style={{ gap: 10 }}>
        <h2 id="opt-in" style={{ fontSize: "var(--sv-fs-lg)" }}>{t("opt.assetsTitle")}</h2>
        <div className="tbl-wrap">
          <table className="tbl" style={{ minWidth: 420 }}>
            <thead><tr><th scope="col" className="l">{t("opt.ticker")}</th><th scope="col">{t("opt.min")}</th><th scope="col">{t("opt.max")}</th><th scope="col" /></tr></thead>
            <tbody>{assets.map((a, i) => (
              <tr key={i}>
                <td className="l"><input value={a.ticker} onChange={(e) => set(i, { ticker: e.target.value.toUpperCase() })} aria-label={t("opt.ticker")}
                                         placeholder="SPY" style={{ width: 120 }} /></td>
                <td><input type="number" min={0} max={100} step={1} value={a.min} onChange={(e) => set(i, { min: Math.round(Number(e.target.value)) })}
                           aria-label={t("opt.min")} style={{ width: 70 }} /> %</td>
                <td><input type="number" min={0} max={100} step={1} value={a.max} onChange={(e) => set(i, { max: Math.round(Number(e.target.value)) })}
                           aria-label={t("opt.max")} style={{ width: 70 }} /> %</td>
                <td><button className="btn secondary small" type="button" disabled={assets.length <= 2}
                            onClick={() => setAssets(assets.filter((_, j) => j !== i))}>{t("common.remove")}</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <div><button className="btn secondary small" type="button" disabled={assets.length >= 10}
                     onClick={() => setAssets([...assets, { ticker: "", min: 0, max: 100 }])}>{t("opt.addAsset")}</button></div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <span>{t("opt.objective")}</span>
          <div className="seg" role="group" aria-label={t("opt.objective")}>
            {(["sharpe", "max_drawdown", "return", "mix"] as const).map((k) => (
              <button key={k} type="button" aria-pressed={obj === k} onClick={() => setObj(k)}>{t(`opt.obj.${k}`)}</button>
            ))}
          </div>
          {obj === "mix" && (["sharpe", "max_drawdown", "return"] as const).map((k) => (
            <label key={k} style={{ display: "flex", gap: 4, alignItems: "center" }}>{t(`opt.obj.${k}`)}
              <input type="number" min={0} max={10} step={0.5} value={mix[k]} onChange={(e) => setMix({ ...mix, [k]: Number(e.target.value) })} style={{ width: 60 }} /></label>
          ))}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <div className="seg" role="group" aria-label={t("cmp.range")}>
            {RANGES.map((x) => <button key={x} type="button" aria-pressed={range === x} onClick={() => setRange(x)}>{t(`cmp.range.${x}`)}</button>)}
          </div>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>{t("opt.rebalance")}
            <select value={rebalance} onChange={(e) => setRebalance(e.target.value as OptimizeIn["rebalance"])}>
              {(["none", "monthly", "quarterly", "yearly"] as const).map((x) => <option key={x} value={x}>{t(`opt.rebalance.${x}`)}</option>)}
            </select></label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>{t("cmp.currency")}
            <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
              {[...new Set([currency, ...CURRENCIES].filter(Boolean))].map((c) => <option key={c} value={c}>{c}</option>)}
            </select></label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>{t("cmp.rf")}
            <input type="number" step="0.1" value={rf} onChange={(e) => setRf(e.target.value)} style={{ width: 70 }} />%</label>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>{t("opt.samples")}
            <select value={samples} onChange={(e) => setSamples(Number(e.target.value))}>
              {[5000, 20000, 50000].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}
            </select></label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>{t("opt.seed")}
            <input type="number" min={0} value={seed} onChange={(e) => setSeed(Math.max(0, Math.round(Number(e.target.value))))} style={{ width: 90 }} /></label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>{t("opt.oos")}
            <select value={oos} onChange={(e) => setOos(Number(e.target.value))}>
              {[0, 0.2, 0.3, 0.4].map((x) => <option key={x} value={x}>{x ? t("opt.oosShare", { pct: Math.round(x * 100) }) : t("opt.oosNone")}</option>)}
            </select></label>
          <button className="btn primary" type="button" disabled={run.isPending} onClick={() => run.mutate()}>
            {run.isPending ? t("opt.running") : t("opt.run")}</button>
        </div>
        <span className="muted small">{t("opt.method")}</span>
      </section>

      {run.error && <div role="alert" className="banner err">{run.error instanceof ApiError ? run.error.message : String(run.error)}</div>}

      {r && (
        <>
          <section className="card" aria-labelledby="opt-best" style={{ gap: 10 }}>
            <h2 id="opt-best" style={{ fontSize: "var(--sv-fs-lg)" }}>{t("opt.bestTitle")}</h2>
            <span className="muted small">{t("opt.runInfo", { start: win!.start, end: win!.end, days: win!.days, samples: r.samples.toLocaleString(),
                                                              seed: r.seed, ccy: r.currency, freq: t(`cmp.frequency.${r.frequency}`), data: r.data_version })}</span>
            {win?.limited_by && <div className="banner info">{t("cmp.limited", { ref: win.limited_by, start: win.start })}</div>}
            <div className="tbl-wrap">
              <table className="tbl" style={{ minWidth: 360 }}>
                <tbody>{Object.entries(r.weights).sort((a, b) => b[1] - a[1]).map(([tk, w]) => (
                  <tr key={tk}><th scope="row" className="l">{tk}</th>
                    <td style={{ width: "60%" }}><div className="bar"><i style={{ width: `${w}%` }} /></div></td><td><strong>{w}%</strong></td></tr>
                ))}</tbody>
              </table>
            </div>
            <div className="kv">
              {KEYS.map((k) => <div key={k}><span className="muted small">{t(`opt.m.${k}`)}</span><strong>{metric(k, r.metrics[k])}</strong></div>)}
            </div>
            <span className="muted small">{t(r.refined ? "opt.refined" : "opt.notRefined")}</span>
            {Object.keys(r.breakdown).length > 1 && (
              <div className="tbl-wrap">
                <table className="tbl" style={{ minWidth: 420 }}>
                  <thead><tr><th scope="col" className="l">{t("opt.objective")}</th><th scope="col">{t("opt.value")}</th><th scope="col">{t("opt.z")}</th>
                    <th scope="col">{t("opt.weight")}</th><th scope="col">{t("opt.contribution")}</th></tr></thead>
                  <tbody>{Object.entries(r.breakdown).map(([k, b]) => {
                    const v = b as { value: number; z: number; weight: number; contribution: number };
                    return <tr key={k}><th scope="row" className="l">{t(`opt.obj.${k}`)}</th><td>{metric(k, v.value)}</td><td>{v.z.toFixed(2)}</td>
                      <td>{f.pct(v.weight, 0, false)}</td><td>{v.contribution.toFixed(2)}</td></tr>;
                  })}</tbody>
                </table>
              </div>
            )}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("opt.portfolioName")} maxLength={80} aria-label={t("opt.portfolioName")} />
              <button className="btn secondary small" type="button" disabled={!name.trim() || savePf.isPending} onClick={() => savePf.mutate()}>{t("opt.savePortfolio")}</button>
              <Link to="/compare" className="small">{t("opt.toCompare")}</Link>
            </div>
            {msg && <div role="status" className="banner info">{msg}</div>}
          </section>

          {o && (
            <section className="card" aria-labelledby="opt-oos" style={{ gap: 8 }}>
              <h2 id="opt-oos" style={{ fontSize: "var(--sv-fs-lg)" }}>{t("opt.oosTitle")}</h2>
              <span className="muted small">{t("opt.oosIntro")}</span>
              <div className="tbl-wrap">
                <table className="tbl" style={{ minWidth: 520 }}>
                  <thead><tr><th scope="col" className="l">{t("cmp.metric")}</th>
                    <th scope="col">{t("opt.inSample", { start: o.in_sample.start, end: o.in_sample.end })}</th>
                    <th scope="col">{t("opt.outSample", { start: o.out_of_sample.start, end: o.out_of_sample.end })}</th>
                    <th scope="col">{t("opt.equalWeight")}</th></tr></thead>
                  <tbody>{KEYS.map((k) => (
                    <tr key={k}><th scope="row" className="l">{t(`opt.m.${k}`)}</th><td>{metric(k, o.in_sample.metrics[k])}</td>
                      <td><strong>{metric(k, o.out_of_sample.metrics[k])}</strong></td><td>{metric(k, o.out_of_sample.equal_weight[k])}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </section>
          )}

          <section className="card" aria-labelledby="opt-frontier" style={{ gap: 8 }}>
            <h2 id="opt-frontier" style={{ fontSize: "var(--sv-fs-lg)" }}>{t("opt.frontierTitle")}</h2>
            <span className="muted small">{t("opt.frontierIntro")}</span>
            <FrontierChart data={r} />
          </section>

          <section className="card" aria-labelledby="opt-top" style={{ gap: 8 }}>
            <h2 id="opt-top" style={{ fontSize: "var(--sv-fs-lg)" }}>{t("opt.topTitle")}</h2>
            <div className="tbl-wrap">
              <table className="tbl" style={{ minWidth: 260 + Object.keys(r.weights).length * 70 }}>
                <thead><tr><th scope="col">#</th>{Object.keys(r.weights).map((tk) => <th key={tk} scope="col">{tk}</th>)}
                  {KEYS.map((k) => <th key={k} scope="col">{t(`opt.m.${k}`)}</th>)}</tr></thead>
                <tbody>{r.top.map((c, i) => {
                  const row = c as { weights: Record<string, number>; metrics: Record<string, number> };
                  return <tr key={i}><td>{i + 1}</td>{Object.keys(r.weights).map((tk) => <td key={tk}>{row.weights[tk]}%</td>)}
                    {KEYS.map((k) => <td key={k}>{metric(k, row.metrics[k])}</td>)}</tr>;
                })}</tbody>
              </table>
            </div>
          </section>
        </>
      )}
      <footer className="foot">{t("app.disclaimer")}</footer>
    </main>
  );
}
