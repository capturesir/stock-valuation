import { ApiError, type CompareIn, type CompareOut } from "@sv/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { BacktestScorecardCard } from "../components/BacktestScorecardCard";
import { CompareChart, seriesColor, seriesDashed } from "../components/CompareChart";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useMe } from "../lib/session";

type Line = { kind: "ticker" | "portfolio"; ref: string; label?: string };
const RANGES = ["1y", "2y", "3y", "5y", "10y", "ytd", "max", "custom"] as const;
const CURRENCIES = ["USD", "HKD", "CNY", "EUR", "GBP", "JPY"];
const PCT = new Set(["total_return", "annualised_return", "volatility", "max_drawdown", "alpha", "tracking_error", "months_beating",
                     "best_month", "worst_month", "up_capture", "down_capture"]);
const METRICS = ["total_return", "annualised_return", "volatility", "sharpe", "sortino", "max_drawdown", "drawdown_days", "calmar",
                 "beta", "alpha", "correlation", "tracking_error", "information_ratio", "up_capture", "down_capture",
                 "months_beating", "best_month", "worst_month"] as const;
const PERIODS = ["1m", "3m", "6m", "ytd", "1y", "3y", "5y"] as const;

/** Comparison chart (roadmap §5.4): up to 10 stocks or saved portfolios against one benchmark. */
export function ComparePage() {
  const { t, f } = useI18n();
  const me = useMe().data;
  const qc = useQueryClient();
  const [lines, setLines] = useState<Line[]>([{ kind: "ticker", ref: "SPY" }]);
  const [benchmark, setBenchmark] = useState(0);
  const [range, setRange] = useState<CompareIn["range"]>("3y");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [currency, setCurrency] = useState<string>("");
  const [rf, setRf] = useState("4");
  const [mode, setMode] = useState<"index" | "pct">("index");
  const [selected, setSelected] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [name, setName] = useState("");
  const [savedId, setSavedId] = useState<string | undefined>();
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (me && !currency) setCurrency(me.prefs?.display_currency || "USD"); }, [me, currency]);

  const body: CompareIn = useMemo(() => ({
    lines: lines.map(({ kind, ref }) => ({ kind, ref })), benchmark, range, currency: currency || undefined,
    rf: Number(rf) / 100 || 0, start: range === "custom" ? start || undefined : undefined, end: end || undefined,
  }), [lines, benchmark, range, currency, rf, start, end]);
  const q = useQuery({ queryKey: ["compare", body], queryFn: () => api.compare(body), enabled: lines.length > 0 && !!currency,
                       retry: false, staleTime: 5 * 60e3 });
  const data: CompareOut | undefined = q.data;
  const win = data?.window as { start: string; end: string; limited_by?: string | null } | undefined;
  useEffect(() => {          // default shading: the first line that is not the benchmark
    if (!data) return;
    if (!selected || !data.lines.some((l) => l.key === selected)) setSelected(data.lines.find((l) => l.key !== data.benchmark)?.key ?? null);
  }, [data, selected]);

  const tickerRefs = useMemo(() => lines.filter((l) => l.kind === "ticker").map((l) => l.ref), [lines]);
  const peerFin = useQuery({
    queryKey: ["peer-fin", tickerRefs],
    queryFn: () => api.peerFinancialCompare(tickerRefs),
    enabled: tickerRefs.length >= 2,
    retry: false,
    staleTime: 5 * 60e3,
  });

  const portfolios = useQuery({ queryKey: ["portfolios"], queryFn: api.portfolios });
  const saved = useQuery({ queryKey: ["comparisons"], queryFn: api.comparisons });
  const save = useMutation({ mutationFn: () => api.saveComparison(name.trim(), body, savedId),
                             onSuccess: (c) => { setSavedId(c.id); setMsg(t("cmp.saved", { name: c.name })); void qc.invalidateQueries({ queryKey: ["comparisons"] }); },
                             onError: (e) => setMsg((e as Error).message) });
  const remove = useMutation({ mutationFn: (id: string) => api.deleteComparison(id),
                               onSuccess: () => { setSavedId(undefined); void qc.invalidateQueries({ queryKey: ["comparisons"] }); } });

  const add = (e: FormEvent) => {
    e.preventDefault();
    const v = input.trim().toUpperCase();
    if (!v || lines.length >= 10 || lines.some((l) => l.kind === "ticker" && l.ref === v)) return;
    setLines([...lines, { kind: "ticker", ref: v }]);
    setInput("");
  };
  const addPortfolio = (id: string) => {
    const p = portfolios.data?.find((x) => x.id === id);
    if (!p || lines.length >= 10 || lines.some((l) => l.kind === "portfolio" && l.ref === id)) return;
    setLines([...lines, { kind: "portfolio", ref: id, label: p.name }]);
  };
  const drop = (i: number) => {
    setLines(lines.filter((_, j) => j !== i));
    setBenchmark(benchmark === i ? 0 : benchmark > i ? benchmark - 1 : benchmark);
  };
  const load = (id: string) => {
    const c = saved.data?.find((x) => x.id === id);
    if (!c) return;
    const s = c.settings;
    setLines(s.lines.map((l) => ({ kind: l.kind ?? "ticker", ref: l.ref, label: l.kind === "portfolio" ? portfolios.data?.find((p) => p.id === l.ref)?.name : undefined })));
    setBenchmark(s.benchmark ?? 0); setRange(s.range ?? "3y"); setStart(s.start ?? ""); setEnd(s.end ?? "");
    setCurrency(s.currency ?? currency); setRf(String(((s.rf ?? 0) * 100).toFixed(2)).replace(/\.?0+$/, "")); setName(c.name); setSavedId(c.id);
  };
  const exportAs = async (format: "xlsx" | "csv") => {
    try {
      const blob = await api.compareExport(body, format);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `compare_${new Date().toISOString().slice(0, 10)}.${format}`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { setMsg((e as Error).message); }
  };
  const metric = (k: string, v: unknown) => {
    if (v == null) return "—";
    const n = Number(v);
    if (k === "drawdown_days") return t("cmp.days", { n });
    return PCT.has(k) ? f.pct(n, 1, k === "alpha" || k === "best_month" || k === "worst_month") : n.toFixed(2);
  };

  return (
    <main className="main">
      <div><h1 style={{ fontSize: "var(--sv-fs-4xl)" }}>{t("cmp.title")}</h1>
        <p className="muted" style={{ margin: "6px 0 0" }}>{t("cmp.subtitle")}</p></div>

      <section className="card" aria-labelledby="cmp-in" style={{ gap: 10 }}>
        <h2 id="cmp-in" className="sr-only">{t("cmp.inputs")}</h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          {lines.map((l, i) => (
            <span key={`${l.kind}:${l.ref}`} className="chip">
              <span className="cmp-swatch" style={{ borderColor: seriesColor(i), borderTopStyle: seriesDashed(i) ? "dashed" : "solid" }} />
              {l.label ?? l.ref}
              <button type="button" className={benchmark === i ? "on" : ""} aria-pressed={benchmark === i} title={t("cmp.setBenchmark")}
                      onClick={() => setBenchmark(i)}>{benchmark === i ? "★" : "☆"}</button>
              <button type="button" aria-label={t("common.remove")} onClick={() => drop(i)}>×</button>
            </span>
          ))}
          <form onSubmit={add} style={{ display: "flex", gap: 6 }}>
            <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={t("cmp.addPlaceholder")} aria-label={t("cmp.add")}
                   autoCapitalize="characters" disabled={lines.length >= 10} style={{ width: 150 }} />
            <button className="btn secondary small" type="submit" disabled={lines.length >= 10}>{t("cmp.add")}</button>
          </form>
          {(portfolios.data?.length ?? 0) > 0 && (
            <select aria-label={t("cmp.addPortfolio")} value="" onChange={(e) => addPortfolio(e.target.value)} disabled={lines.length >= 10}>
              <option value="">{t("cmp.addPortfolio")}</option>
              {portfolios.data!.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          )}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <div className="seg" role="group" aria-label={t("cmp.range")}>
            {RANGES.map((r) => <button key={r} type="button" aria-pressed={range === r} onClick={() => setRange(r)}>{t(`cmp.range.${r}`)}</button>)}
          </div>
          {range === "custom" && <>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} aria-label={t("cmp.start")} />
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} aria-label={t("cmp.end")} />
          </>}
          <div className="seg" role="group" aria-label={t("cmp.mode")}>
            {(["index", "pct"] as const).map((m) => <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}>{t(`cmp.mode.${m}`)}</button>)}
          </div>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>{t("cmp.currency")}
            <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
              {[...new Set([currency, ...CURRENCIES].filter(Boolean))].map((c) => <option key={c} value={c}>{c}</option>)}
            </select></label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>{t("cmp.rf")}
            <input type="number" step="0.1" min="-5" max="50" value={rf} onChange={(e) => setRf(e.target.value)} style={{ width: 70 }} />%</label>
        </div>
      </section>

      {q.isFetching && !data && <p className="muted">{t("common.loading")}</p>}
      {q.error && <div role="alert" className="banner err">{q.error instanceof ApiError ? q.error.message : String(q.error)}</div>}

      {data && (
        <section className="card" aria-labelledby="cmp-chart" style={{ gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <h2 id="cmp-chart" style={{ fontSize: "var(--sv-fs-lg)" }}>{t("cmp.window", { start: win!.start, end: win!.end, ccy: data.currency })}</h2>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn secondary small" type="button" onClick={() => void exportAs("xlsx")}>{t("std.export.xlsx")}</button>
              <button className="btn secondary small" type="button" onClick={() => void exportAs("csv")}>{t("std.export.csv")}</button>
            </div>
          </div>
          {win?.limited_by && <div className="banner info">{t("cmp.limited", { ref: win.limited_by, start: win.start })}</div>}
          <div className="cmp-legend" role="group" aria-label={t("cmp.legend")}>
            {data.lines.map((l, i) => (
              <button key={l.key} type="button" aria-pressed={selected === l.key} onClick={() => setSelected(l.key)}
                      title={l.key === data.benchmark ? t("cmp.isBenchmark") : t("cmp.selectHint")}>
                <span className="cmp-swatch" style={{ borderColor: seriesColor(i), borderTopStyle: seriesDashed(i) ? "dashed" : "solid" }} />
                {l.ref}{l.key === data.benchmark ? ` ★` : ""}
              </button>
            ))}
          </div>
          <span className="muted small">{t("cmp.shadeNote")}</span>
          <CompareChart data={data} mode={mode} selected={selected} />
          <span className="muted small">{t(`cmp.frequencyNote.${data.frequency}`)}</span>

          <h3 style={{ margin: "8px 0 0" }}>{t("cmp.periods")}</h3>
          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 520 }}>
              <thead><tr><th scope="col" className="l">{t("cmp.line")}</th>{PERIODS.map((p) => <th key={p} scope="col">{t(`cmp.period.${p}`)}</th>)}</tr></thead>
              <tbody>{data.periods.map((p, i) => (
                <tr key={p.key as string}><th scope="row" className="l">{data.lines[i].ref}{data.lines[i].key === data.benchmark ? " ★" : ""}</th>
                  {PERIODS.map((k) => <td key={k}>{p[k] == null ? "—" : <span className={Number(p[k]) >= 0 ? "pos" : "neg"}>{f.pct(Number(p[k]), 1, true)}</span>}</td>)}</tr>
              ))}</tbody>
            </table>
          </div>

          <h3 style={{ margin: "8px 0 0" }}>{t("cmp.metrics")}</h3>
          <span className="muted small">{t("cmp.metricsNote", { rf: f.pct(data.rf, 1, false) })}</span>
          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 160 + data.lines.length * 100 }}>
              <thead><tr><th scope="col" className="l">{t("cmp.metric")}</th>
                {data.lines.map((l) => <th key={l.key} scope="col">{l.ref}{l.key === data.benchmark ? " ★" : ""}</th>)}</tr></thead>
              <tbody>{METRICS.map((k) => (
                <tr key={k}><th scope="row" className="l">{t(`metric.${k}`)}</th>
                  {data.metrics.map((m) => <td key={m.key as string}>{metric(k, m[k])}</td>)}</tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      )}

      {peerFin.data && (peerFin.data.companies?.length ?? 0) > 0 && (
        <section className="card" aria-labelledby="cmp-peer-fin" style={{ gap: 8 }}>
          <h2 id="cmp-peer-fin" style={{ fontSize: "var(--sv-fs-lg)" }}>{t("analytics.peer.title")}</h2>
          <span className="muted small">{t("analytics.peer.subtitle")}</span>
          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 220 + (peerFin.data.companies?.length ?? 0) * 110 }}>
              <thead><tr>
                <th scope="col" className="l">{t("cmp.metric")}</th>
                {(peerFin.data.companies ?? []).map((c) => (
                  <th key={c.ticker} scope="col">{c.ticker}<br /><span className="muted small">{c.period ?? "—"} {c.grade ? `(${c.grade})` : ""}</span></th>
                ))}
                <th scope="col">{t("analytics.peer.median")}</th>
              </tr></thead>
              <tbody>
                {(peerFin.data.metrics ?? []).map((k) => {
                  const fmtVal = (val: number | null | undefined) => {
                    if (val == null) return "—";
                    if (k === "debt_to_equity" || k === "cfo_to_net_income") return `${val.toFixed(2)}×`;
                    return f.pct(val, 1, k === "revenue_growth_yoy");
                  };
                  return (
                    <tr key={k}>
                      <th scope="row" className="l">{t(`analytics.peer.m.${k}`)}</th>
                      {(peerFin.data.companies ?? []).map((c) => <td key={c.ticker}>{fmtVal(c.values?.[k])}</td>)}
                      <td><strong>{fmtVal(peerFin.data.medians?.[k])}</strong></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {(peerFin.data.missing?.length ?? 0) > 0 && (
            <span className="muted small">{t("analytics.peer.missing", { list: (peerFin.data.missing ?? []).join(", ") })}</span>
          )}
        </section>
      )}

      <section className="card" aria-labelledby="cmp-saved" style={{ gap: 8 }}>
        <h2 id="cmp-saved" style={{ fontSize: "var(--sv-fs-lg)" }}>{t("cmp.savedTitle")}</h2>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("cmp.namePlaceholder")} maxLength={80} aria-label={t("cmp.name")} />
          <button className="btn primary small" type="button" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>
            {t(savedId ? "cmp.update" : "cmp.save")}</button>
          {(saved.data?.length ?? 0) > 0 && (
            <select aria-label={t("cmp.load")} value={savedId ?? ""} onChange={(e) => load(e.target.value)}>
              <option value="">{t("cmp.load")}</option>
              {saved.data!.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
          {savedId && <button className="btn secondary small" type="button" onClick={() => remove.mutate(savedId)}>{t("common.remove")}</button>}
        </div>
        {msg && <div role="status" className="banner info">{msg}</div>}
      </section>
      <BacktestScorecardCard tickers={lines.filter((l) => l.kind === "ticker").map((l) => l.ref)} />
      <footer className="foot">{t("app.disclaimer")}</footer>
    </main>
  );
}
