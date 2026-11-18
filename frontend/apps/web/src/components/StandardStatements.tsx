import type { StandardStatements as Std, StdCell, StdRow } from "@sv/api-client";
import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { groupDigits } from "./FiledData";

/** Display order within each statement (the API's order follows the rule file, where formulas come last). */
const ORDER: Record<StdRow["statement"], string[]> = {
  income: ["revenue", "cost_of_revenue", "gross_profit", "research_development", "selling_general_admin", "operating_expenses",
           "operating_income", "interest_expense", "pretax_income", "income_tax", "net_income", "net_income_nci",
           "net_income_parent", "eps_basic", "eps_diluted", "shares_basic_weighted", "shares_diluted_weighted"],
  balance: ["cash", "short_term_investments", "receivables", "inventory", "current_assets", "ppe_net", "goodwill", "intangibles",
            "total_assets", "accounts_payable", "current_liabilities", "debt_current", "debt_noncurrent", "total_liabilities",
            "equity_parent", "equity_nci", "total_equity", "liabilities_and_equity", "shares_outstanding"],
  cash_flow: ["cfo", "depreciation_amortization", "share_based_compensation", "capex", "cfi", "dividends_paid",
              "share_repurchases", "cff", "free_cash_flow"],
};
const TOTALS = new Set(["gross_profit", "operating_income", "net_income_parent", "total_assets", "total_liabilities", "total_equity",
                        "liabilities_and_equity", "cfo", "free_cash_flow"]);
const STATEMENTS: StdRow["statement"][] = ["income", "balance", "cash_flow"];
const GRADE_TAG: Record<string, string> = { A: "ok", B: "warn", C: "bad" };

type Picked = { code: string; year: number } | null;

export function StandardStatements({ ticker, onShowFiled }: { ticker: string; onShowFiled: (concept: string) => void }) {
  const { t, locale } = useI18n();
  const [view, setView] = useState<"latest" | "original">("latest");
  const [period, setPeriod] = useState<"annual" | "quarterly">("annual");
  const [picked, setPicked] = useState<Picked>(null);
  const q = useQuery({ queryKey: ["std", ticker, view, period], queryFn: () => api.standardStatements(ticker, view, period) });
  const d = q.data;
  const millions = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
  const millionsShares = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  const show = (row: StdRow, c: StdCell) => {
    const n = Number(c.value);
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
        </div>
      </div>
      {d.template !== "general" && <div className="banner warn" role="note">{t(`std.template.${d.template}`)}</div>}
      {(d.splits_applied ?? []).length > 0 && <span className="small">{t("std.splits", {
        list: (d.splits_applied ?? []).map((s) => t("std.split", { date: String(s.date), ratio: String(s.ratio) })).join("、") })}</span>}
      <span className="muted small">{t("std.legend")}</span>

      {STATEMENTS.map((st) => {
        const rows = d.rows.filter((r) => r.statement === st)
          .sort((a, b) => ORDER[st].indexOf(a.code) - ORDER[st].indexOf(b.code));
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
                    <tr key={r.code} className={TOTALS.has(r.code) ? "total" : ""}>
                      <th scope="row" className="l">{t(`std.item.${r.code}`)}</th>
                      {r.cells.map((c, i) => (
                        <td key={i} className={d.years[i].kind === "ttm" ? "ttm" : ""}>
                          {c ? (
                            <button type="button" className={`cell${picked?.code === r.code && picked.year === i ? " active" : ""}`}
                                    onClick={() => setPicked(picked?.code === r.code && picked.year === i ? null : { code: r.code, year: i })}
                                    aria-label={`${t(`std.item.${r.code}`)} ${d.years[i].label}`}>
                              {show(r, c)}
                              {c.method === "formula" && <sup className="muted" title={t("std.mark.formula")}>ƒ</sup>}
                              {c.method === "derived" && <sup className="muted" title={t("std.mark.derived")}>Δ</sup>}
                              {c.change === "restated" && <sup className="mark-r" title={t("std.mark.restated")}>R</sup>}
                              {(c.split_factor !== "1" || c.change === "split_adjusted") && <sup className="muted" title={t("std.mark.split_adjusted")}>S</sup>}
                            </button>
                          ) : <span className="muted">—</span>}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {sel && picked && <Lineage d={d} row={sel} year={picked.year} view={view} onShowFiled={onShowFiled} />}
          </Fragment>
        );
      })}
      <Checks d={d} />
    </section>
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

function Lineage({ d, row, year, view, onShowFiled }: { d: Std; row: StdRow; year: number; view: "latest" | "original";
                                                      onShowFiled: (concept: string) => void }) {
  const { t } = useI18n();
  const c = row.cells[year];
  const y = d.years[year];
  if (!c) return null;
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
      {c.change === "restated" && c.original_value != null && c.latest_value != null && (
        <span>{t(`std.change.${c.change}`, { original: groupDigits(c.original_value), latest: groupDigits(c.latest_value) })}
          {" "}{t(view === "latest" ? "std.showing.latest" : "std.showing.original")}</span>
      )}
    </div>
  );
}
