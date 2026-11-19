import type { PlanChoice, StandardStatements as Std, StdCell, StdRow } from "@sv/api-client";
import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { AdjustCell, AdjustmentPlans } from "./AdjustmentPlans";
import { groupDigits } from "./FiledData";

const STATEMENTS: StdRow["statement"][] = ["income", "balance", "cash_flow"];
const GRADE_TAG: Record<string, string> = { A: "ok", B: "warn", C: "bad" };

type Picked = { code: string; year: number } | null;

export function StandardStatements({ ticker, onShowFiled }: { ticker: string; onShowFiled: (concept: string) => void }) {
  const { t, locale } = useI18n();
  const [view, setView] = useState<"latest" | "original">("latest");
  const [period, setPeriod] = useState<"annual" | "quarterly">("annual");
  const [display, setDisplay] = useState<"values" | "common">("values");
  const [picked, setPicked] = useState<Picked>(null);
  const [plan, setPlan] = useState<PlanChoice>("auto");
  const q = useQuery({ queryKey: ["std", ticker, view, period, plan], queryFn: () => api.standardStatements(ticker, view, period, 10, plan) });
  const d = q.data;
  const millions = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
  const millionsShares = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  const pctFmt = new Intl.NumberFormat(locale, { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const show = (row: StdRow, c: StdCell, i: number) => {
    const n = Number(c.value);
    if (display === "common") {
      if (row.unit !== "money") return "—";
      const base = d?.rows.find((r) => r.code === (row.statement === "balance" ? "total_assets" : "revenue"))?.cells[i];
      return base && Number(base.value) ? pctFmt.format(n / Number(base.value)).replace(/^-/, "−") : "—";
    }
    const s = row.unit === "per_share" ? groupDigits(c.value)
      : (row.unit === "shares" ? millionsShares : millions).format(n / 1e6);
    return s.replace(/^-/, "−");
  };

  if (q.isLoading) return <p className="muted">{t("common.loading")}</p>;
  if (q.error) return <div role="alert" className="banner err">{(q.error as Error).message}</div>;
  if (!d) return null;
  if (!d.years.length) return <div className="banner info">{t("std.noYears")}</div>;
  const grades: Record<string, string> = d.checks.grades ?? {};

  return (
    <section className="card" aria-labelledby="std-h" style={{ gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h2 id="std-h">{t("std.title")}</h2>
          <span className="muted small">{t("std.subtitle", { ccy: d.currency ?? "—", ruleset: d.ruleset, version: d.ruleset_version })}</span>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <div className="seg" role="group" aria-label={t("std.period")}>
            {(["annual", "quarterly"] as const).map((v) => (
              <button key={v} type="button" aria-pressed={period === v} onClick={() => { setPeriod(v); setPicked(null); }}>{t(`std.period.${v}`)}</button>
            ))}
          </div>
          <div className="seg" role="group" aria-label={t("std.view")}>
            {(["latest", "original"] as const).map((v) => (
              <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)} title={t(`std.view.${v}.hint`)}>{t(`std.view.${v}`)}</button>
            ))}
          </div>
          <div className="seg" role="group" aria-label={t("std.display")}>
            {(["values", "common"] as const).map((v) => (
              <button key={v} type="button" aria-pressed={display === v} onClick={() => setDisplay(v)}>{t(`std.display.${v}`)}</button>
            ))}
          </div>
          <div className="seg" role="group" aria-label={t("adj.plan")}>
            {(["auto", "central", "personal", "none"] as const).map((v) => (
              <button key={v} type="button" aria-pressed={plan === v} onClick={() => setPlan(v)} title={t(`adj.choice.${v}.hint`)}>{t(`adj.choice.${v}`)}</button>
            ))}
          </div>
          <a className="btn secondary small" href={api.statementsExportUrl(ticker, "xlsx", view, period, plan)}>{t("std.export.xlsx")}</a>
          <a className="btn secondary small" href={api.statementsExportUrl(ticker, "csv", view, period, plan)}>{t("std.export.csv")}</a>
        </div>
      </div>
      {d.template !== "general" && <div className="banner info" role="note">{t(`std.template.${d.template}`)}</div>}
      {(d.splits_applied ?? []).length > 0 && <span className="small">{t("std.splits", {
        list: (d.splits_applied ?? []).map((s) => t("std.split", { date: String(s.date), ratio: String(s.ratio) })).join("、") })}</span>}
      <span className="small">{d.plan ? t(`adj.using.${d.plan.kind}`, { v: d.plan.version }) : t("adj.using.none")}
        {(d.adjustments_not_shown ?? []).length > 0 && <span className="muted"> · {t("adj.notShown", { list: (d.adjustments_not_shown ?? []).join("、") })}</span>}</span>
      <span className="muted small">{t("std.legend")}</span>
      {display === "common" && <span className="small">{t("std.commonNote")}</span>}

      {STATEMENTS.map((st) => {
        const rows = d.rows.filter((r) => r.statement === st);        // already in display order
        const sel = picked && rows.find((r) => r.code === picked.code);
        return (
          <Fragment key={st}>
            <h3 style={{ margin: "8px 0 0" }}>{t(`std.statement.${st}`)}</h3>
            <div className="tbl-wrap">
              <table className="tbl std" style={{ minWidth: 160 + d.years.length * 110 }}>
                <thead><tr>
                  <th scope="col" className="l">{t("std.col.item")}</th>
                  {d.years.map((y) => <th key={y.label} scope="col" className={y.kind === "ttm" ? "ttm" : ""} title={`${y.start} – ${y.end}`}>
                    {y.kind === "ttm" ? t("std.ttm") : y.label}<br /><span className="muted small">{y.end}</span>
                    {grades[y.label] && <><br /><span className={`tag ${GRADE_TAG[grades[y.label]]}`} title={t(`check.grade.${grades[y.label]}`)}>{grades[y.label]}</span></>}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.code} className={r.total ? "total" : ""}>
                      <th scope="row" className="l">{t(`std.item.${r.code}`)}</th>
                      {r.cells.map((c, i) => (
                        <td key={i} className={d.years[i].kind === "ttm" ? "ttm" : ""}>
                          {c ? (
                            <button type="button" className={`cell${picked?.code === r.code && picked.year === i ? " active" : ""}`}
                                    onClick={() => setPicked(picked?.code === r.code && picked.year === i ? null : { code: r.code, year: i })}
                                    aria-label={`${t(`std.item.${r.code}`)} ${d.years[i].label}`}>
                              {show(r, c, i)}
                              {c.method === "formula" && <sup className="muted" title={t("std.mark.formula")}>ƒ</sup>}
                              {c.method === "derived" && <sup className="muted" title={t("std.mark.derived")}>Δ</sup>}
                              {c.change === "restated" && <sup className="mark-r" title={t("std.mark.restated")}>R</sup>}
                              {(c.split_factor !== "1" || c.change === "split_adjusted") && <sup className="muted" title={t("std.mark.split_adjusted")}>S</sup>}
                              {c.adjustment && <sup className="mark-a" title={t("adj.mark")}>A</sup>}
                            </button>
                          ) : d.years[i].kind === "ttm" ? <span className="muted">—</span> : (
                            <button type="button" className={`cell muted${picked?.code === r.code && picked.year === i ? " active" : ""}`}
                                    onClick={() => setPicked(picked?.code === r.code && picked.year === i ? null : { code: r.code, year: i })}
                                    aria-label={`${t(`std.item.${r.code}`)} ${d.years[i].label} — ${t("adj.supply")}`}>—</button>)}
                        </td>
                      ))}
                    </tr>
                  ))}
                  {st === "income" && display === "values" && (d.share_classes ?? []).map((sc) => {
                    const label = t("std.class.row", { item: t(`std.item.${sc.item}`), cls: sc.label });
                    return (
                      <tr key={`${sc.member}-${sc.item}`} className="sub">
                        <th scope="row" className="l">{label}</th>
                        {sc.cells.map((c, i) => (
                          <td key={i} className={d.years[i].kind === "ttm" ? "ttm" : ""}>
                            {c ? <a href={c.document_url ?? c.index_url} target="_blank" rel="noreferrer" aria-label={`${label} ${d.years[i].label}`}
                                    title={t("std.class.hint", { cls: sc.member, form: c.form, filed: c.filed })}>
                              {groupDigits(c.value).replace(/^-/, "−")}
                              {c.split_factor !== "1" && <sup className="muted" title={t("std.mark.split_adjusted")}>S</sup>}</a>
                              : <span className="muted">—</span>}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {sel && picked && <Lineage d={d} row={sel} year={picked.year} view={view} onShowFiled={onShowFiled} ticker={ticker} />}
          </Fragment>
        );
      })}
      <Ratios d={d} />
      <Checks d={d} />
      <AdjustmentPlans ticker={ticker} />
    </section>
  );
}

function Ratios({ d }: { d: Std }) {
  const { t, f } = useI18n();
  const [picked, setPicked] = useState<{ code: string; i: number } | null>(null);
  const r = d.ratios;
  const fmt = (kind: string, v: string) => {
    const n = Number(v);
    if (kind === "pct") return f.pct(n, 1, false);
    if (kind === "days") return n.toFixed(0);
    return `${n.toFixed(2)}×`;
  };
  const sel = picked && r.rows.find((x) => x.code === picked.code)?.cells[picked.i];
  const inputText = (k: string, v: unknown) => {
    if (k === "tax_rate") return t("std.taxRate", { rate: f.pct(Number(v), 1, false) });
    const label = t(`std.item.${k}`);
    if (v && typeof v === "object") {
      const o = v as { end: string; year_ago: string };
      return `${label} ${t("std.ratioAvg", { end: groupDigits(o.end), ago: groupDigits(o.year_ago) })}`;
    }
    return `${label} ${groupDigits(String(v))}`;
  };
  const cagr = r.cagr ?? [];
  return (
    <div style={{ borderTop: "1px solid var(--sv-line-faint)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
      <h3 style={{ margin: 0 }}>{t("std.ratios")}</h3>
      <span className="muted small">{t("std.ratiosIntro", { version: r.version })}</span>
      <div className="tbl-wrap">
        <table className="tbl std" style={{ minWidth: 160 + d.years.length * 110 }}>
          <thead><tr><th scope="col" className="l">{t("std.col.item")}</th>
            {d.years.map((y) => <th key={y.label} scope="col" className={y.kind === "ttm" ? "ttm" : ""}>{y.kind === "ttm" ? t("std.ttm") : y.label}</th>)}</tr></thead>
          <tbody>
            {r.rows.map((row) => (
              <tr key={row.code}>
                <th scope="row" className="l">{t(`ratio.${row.code}`)}</th>
                {row.cells.map((c, i) => (
                  <td key={i} className={d.years[i].kind === "ttm" ? "ttm" : ""}>
                    {c ? <button type="button" className={`cell${picked?.code === row.code && picked.i === i ? " active" : ""}`}
                                 onClick={() => setPicked(picked?.code === row.code && picked.i === i ? null : { code: row.code, i })}>
                      {fmt(row.kind, c.value)}</button> : <span className="muted">—</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sel && picked && (
        <div className="banner info" role="region" aria-label={t("std.ratioInputs")} style={{ flexDirection: "column", alignItems: "flex-start", gap: 4 }}>
          <strong>{t(`ratio.${picked.code}`)} · {d.years[picked.i].kind === "ttm" ? t("std.ttm") : d.years[picked.i].label}</strong>
          <span>{t("std.ratioInputs")}：{Object.entries(sel.inputs ?? {}).map(([k, v]) => inputText(k, v)).join("；")}</span>
        </div>
      )}
      <h3 style={{ margin: "8px 0 0" }}>{t("std.growth")}</h3>
      <span className="muted small">{t("std.growthIntro")}</span>
      <div className="tbl-wrap">
        <table className="tbl std" style={{ minWidth: 160 + d.years.length * 110 }}>
          <tbody>
            {r.growth.map((g) => (
              <tr key={g.code}>
                <th scope="row" className="l">{t(`std.item.${g.code}`)}</th>
                {g.cells.map((c, i) => <td key={i} className={d.years[i].kind === "ttm" ? "ttm" : ""}>
                  {c != null ? <span className={Number(c) >= 0 ? "pos" : "neg"}>{f.pct(Number(c), 1, true)}</span> : <span className="muted">—</span>}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cagr.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <strong>{t("std.cagr")}</strong>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px" }}>
            {[...new Set(cagr.map((c) => c.code))].map((code) => (
              <span key={code} className="small">{t(`std.item.${code}`)}：{cagr.filter((c) => c.code === code)
                .map((c) => `${t("std.cagrYears", { n: c.years })} ${f.pct(Number(c.value), 1, true)}`).join(" · ")}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Checks({ d }: { d: Std }) {
  const { t, f } = useI18n();
  const c = d.checks;
  const failures = c.failures ?? [];
  const fmt = (v: unknown) => (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v) ? groupDigits(v) : String(v));
  return (
    <div style={{ borderTop: "1px solid var(--sv-line-faint)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
      <h3 style={{ margin: 0 }}>{t("check.title")}</h3>
      <span className="muted small">{t("check.intro", { version: c.version })}{" "}
        {c.yahoo_fetched_at ? t("check.yahoo", { at: f.datetime(c.yahoo_fetched_at) }) : t("check.noYahoo")}</span>
      {failures.length === 0 ? <span>{t("check.allPassed")}</span> : (
        <div className="tbl-wrap">
          <table className="tbl" style={{ minWidth: 640 }}>
            <thead><tr><th scope="col" className="l">{t("check.col.period")}</th><th scope="col" className="l">{t("check.col.check")}</th>
              <th scope="col" className="l">{t("check.col.values")}</th><th scope="col">{t("check.col.diff")}</th></tr></thead>
            <tbody>
              {failures.map((x, i) => (
                <tr key={i}>
                  <td className="l">{x.period === "TTM" ? t("std.ttm") : x.period}</td>
                  <td className="l" style={{ whiteSpace: "normal" }}>
                    <span className={`tag ${x.severity === "error" ? "bad" : x.severity === "warning" ? "warn" : "neutral"}`}>{t(`check.severity.${x.severity}`)}</span>{" "}
                    {t(`check.${x.rule_id}`)}{typeof x.values?.item === "string" ? `（${t(`std.item.${x.values.item}`)}）` : ""}</td>
                  <td className="l" style={{ whiteSpace: "normal" }}>
                    {Object.entries(x.values ?? {}).filter(([k]) => k !== "item").map(([k, v]) => `${t(`check.value.${k}`)} ${fmt(v)}`).join(" · ")}</td>
                  <td>{x.difference != null ? f.pct(Number(x.difference), 2, false) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Lineage({ d, row, year, view, onShowFiled, ticker }: { d: Std; row: StdRow; year: number; view: "latest" | "original";
                                                              onShowFiled: (concept: string) => void; ticker: string }) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const c = row.cells[year];
  const y = d.years[year];
  const yy = d.years[year];
  if (!c) return yy.kind === "ttm" ? null : (
    <div className="banner info" role="region" aria-label={t("std.lineage")} style={{ flexDirection: "column", alignItems: "flex-start", gap: 6 }}>
      {editing ? <AdjustCell ticker={ticker} row={row} period={yy.label} current={null} onDone={() => setEditing(false)} />
        : <button type="button" className="btn secondary small" onClick={() => setEditing(true)}>{t("adj.supply")}</button>}
    </div>
  );
  const adj = c.adjustment as { reason?: string; basis?: string | null; by?: string | null; at?: string | null; from?: string[] } | null | undefined;
  const unit = row.unit === "money" ? d.currency ?? "" : row.unit === "per_share" ? t("std.unit.perShare", { ccy: d.currency ?? "" }) : t("filed.unit.shares");
  const label = (code: string) => t(`std.item.${code}`);
  return (
    <div className="banner info" role="region" aria-label={t("std.lineage")} style={{ flexDirection: "column", alignItems: "flex-start", gap: 6 }}>
      <strong>{label(row.code)} · {y.kind === "ttm" ? t("std.ttm") : y.label}（{y.start} – {y.end}）</strong>
      <span>{t("std.exact")}：<strong>{groupDigits(c.value)}</strong> {unit}</span>
      {c.split_factor !== "1" && c.raw_value != null && (
        <span>{t("std.splitAdjusted", { raw: groupDigits(c.raw_value), factor: c.split_factor })}</span>
      )}
      {(c.method === "derived" || c.method === "ttm") && (
        <span>{c.method === "derived" ? t("std.from.derived", { rule: c.rule_id, concept: c.concept ?? "" })
          : (c.parts ?? []).length ? t("std.from.ttmPerShare", { rule: c.rule_id })
          : t("std.from.ttmSum", { quarters: (c.inputs ?? []).join("、") })}
          {(c.parts ?? []).length > 0 && (
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {(c.parts ?? []).map((p, k) => (
                <li key={k}>{p.sign > 0 ? "＋" : "−"} {groupDigits(p.value)}（{p.period_start ? `${p.period_start} – ${p.period_end}` : p.period_end}，{p.form}，{p.filed}）{" "}
                  <a href={p.document_url ?? p.index_url ?? "#"} target="_blank" rel="noreferrer">{t("std.secDoc")}</a></li>
              ))}
              {c.method === "derived" && (c.inputs ?? []).length > 0 && <li>− {t("std.minusQuarters", { quarters: (c.inputs ?? []).join("、") })}</li>}
            </ul>
          )}
        </span>
      )}
      {c.method === "derived" || c.method === "ttm" ? null : c.method === "concept" ? (
        <span>{t("std.from.concept", { concept: c.concept ?? "", rule: c.rule_id })}
          {c.form && <> · {t("std.from.filing", { form: c.form, filed: c.filed ?? "" })}</>}
          {(c.document_url || c.index_url) && <> · <a href={c.document_url ?? c.index_url ?? "#"} target="_blank" rel="noreferrer">{t("std.secDoc")}</a></>}
          {c.concept && <> · <button type="button" className="linklike" onClick={() => onShowFiled(c.concept!)}>{t("std.showFiled")}</button></>}
        </span>
      ) : (
        <span>{t("std.from.formula", { formula: (c.formula ?? "").split(/\s+/).map((tok) => (tok === "+" || tok === "-" ? (tok === "-" ? "−" : "+") : label(tok.replace("?", "")))).join(" "), rule: c.rule_id })}
          {(c.assumed_zero ?? []).length > 0 && <> · {t("std.assumedZero", { items: (c.assumed_zero ?? []).map(label).join("、") })}</>}</span>
      )}
      {adj?.reason && (
        <span><span className="mark-a">A</span> {t("adj.applied", { from: c.adjusted_from != null ? groupDigits(c.adjusted_from) : t("adj.notFiled"),
                                                                   to: groupDigits(c.value), reason: adj.reason })}
          {adj.basis && <> · {t("adj.basisIs", { basis: adj.basis })}</>}{adj.by && <> · {adj.by} {adj.at?.slice(0, 10)}</>}
          {d.plan && <> · {t(`adj.using.${d.plan.kind}`, { v: d.plan.version })}</>}</span>
      )}
      {adj?.from && <span>{t("adj.follows", { from: (c.adjusted_from != null ? groupDigits(c.adjusted_from) : "—"),
                                              items: adj.from.map((x) => (/^FY/.test(x) ? x : t(`std.item.${x}`))).join("、") })}</span>}
      {y.kind !== "ttm" && (editing
        ? <AdjustCell ticker={ticker} row={row} period={y.label} current={c.value} onDone={() => setEditing(false)} />
        : <button type="button" className="btn secondary small" onClick={() => setEditing(true)}>{t("adj.edit")}</button>)}
      {c.change === "restated" && c.original_value != null && c.latest_value != null && (
        <span>{t(`std.change.${c.change}`, { original: groupDigits(c.original_value), latest: groupDigits(c.latest_value) })}
          {" "}{t(view === "latest" ? "std.showing.latest" : "std.showing.original")}</span>
      )}
    </div>
  );
}
