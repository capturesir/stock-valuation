import { ApiError, type FiledConceptOut, type FiledFactOut } from "@sv/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useIsAdmin } from "../lib/session";
import { StandardStatements } from "./StandardStatements";

/** Concepts most people look for first; the first one the company filed is opened by default. */
const STARTERS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "NetIncomeLoss",
                  "ProfitLoss", "Assets", "Revenue"];

/** "-1234567.50" → "-1,234,567.50" without going through a float, so every digit stays as filed. */
export function groupDigits(value: string): string {
  const m = /^(-?)(\d+)(\.\d+)?$/.exec(value.trim());
  if (!m) return value;
  return `${m[1] === "-" ? "−" : ""}${m[2].replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${m[3] ?? ""}`;
}

const conceptKey = (c: { taxonomy: string; concept: string }) => `${c.taxonomy}:${c.concept}`;
/** Financial-statement taxonomies first, then the cover page (dei), then fee and other schedules. */
const TAXONOMY_ORDER = ["us-gaap", "ifrs-full", "srt", "dei"];
const rank = (tx: string) => { const i = TAXONOMY_ORDER.indexOf(tx); return i < 0 ? TAXONOMY_ORDER.length : i; };
const byTaxonomyThenLabel = (a: FiledConceptOut, b: FiledConceptOut) =>
  rank(a.taxonomy) - rank(b.taxonomy) || (a.label ?? a.concept).localeCompare(b.label ?? b.concept);

/** Layer ② as filed: every number the company reported to the SEC, with a link to the filing. */
export function FiledData({ ticker }: { ticker: string }) {
  const { t, f } = useI18n();
  const isAdmin = useIsAdmin();
  const qc = useQueryClient();
  const filer = useQuery({ queryKey: ["filer", ticker], queryFn: () => api.filer(ticker), retry: false });
  const notSynced = filer.error instanceof ApiError && filer.error.code === "statements_not_synced";
  const concepts = useQuery({ queryKey: ["filed-concepts", ticker], queryFn: () => api.filedConcepts(ticker), enabled: !!filer.data });
  const sync = useMutation({
    mutationFn: () => api.syncStatements(ticker),
    onSuccess: () => {
      for (const k of ["filer", "filed-concepts", "filed-facts", "std"]) void qc.invalidateQueries({ queryKey: [k, ticker] });
    },
  });
  const [picked, setPicked] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [mode, setMode] = useState<"standard" | "filed">("standard");

  useEffect(() => {
    if (picked || !concepts.data?.length) return;
    const first = STARTERS.map((c) => concepts.data.find((x) => x.concept === c)).find(Boolean) ?? concepts.data[0];
    setPicked(conceptKey(first));
  }, [concepts.data, picked]);

  const syncButton = isAdmin && (
    <button className="btn secondary small" type="button" disabled={sync.isPending} onClick={() => sync.mutate()}>
      {sync.isPending ? t("admin.sources.syncing") : t(filer.data ? "filed.resync" : "admin.sources.sync")}</button>
  );
  const syncError = sync.error && <div role="alert" className="banner err">{(sync.error as Error).message}</div>;

  if (filer.isLoading) return <p className="muted">{t("common.loading")}</p>;
  if (notSynced || !filer.data) {
    return (
      <section className="card" aria-labelledby="filed-none-h">
        <h2 id="filed-none-h">{t("filed.none.title", { ticker })}</h2>
        <p className="muted" style={{ margin: 0 }}>{t(isAdmin ? "filed.none.admin" : "filed.none.member")}</p>
        {filer.error && !notSynced && <div role="alert" className="banner err">{(filer.error as Error).message}</div>}
        {syncButton && <div>{syncButton}</div>}
        {syncError}
      </section>
    );
  }

  const fl = filer.data;
  const q = search.trim().toLowerCase();
  const list = (concepts.data ?? []).filter((c) => !q || c.concept.toLowerCase().includes(q) || (c.label ?? "").toLowerCase().includes(q))
    .sort(byTaxonomyThenLabel);
  const current = (concepts.data ?? []).find((c) => conceptKey(c) === picked) ?? null;
  return (
    <>
      <section className="card" aria-labelledby="filer-h">
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <h2 id="filer-h">{fl.name ?? ticker}</h2>
            <span className="muted small">
              {[`CIK ${fl.cik}`, fl.sic_description, fl.fiscal_year_end && t("filed.fye", { mmdd: `${fl.fiscal_year_end.slice(0, 2)}-${fl.fiscal_year_end.slice(2)}` }),
                t("filed.counts", { filings: fl.filings.toLocaleString(), facts: fl.facts.toLocaleString() }),
                fl.synced_at && t("filed.synced", { at: f.datetime(fl.synced_at) })].filter(Boolean).join(" · ")}
            </span>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <a className="btn secondary small" href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${fl.cik}`}
               target="_blank" rel="noreferrer">{t("filed.secPage")}</a>
            {syncButton}
          </div>
        </div>
        {sync.data && <div role="status" className="banner info">{t(sync.data.unchanged ? "admin.sources.unchanged" : "admin.sources.synced", {
          ticker, name: sync.data.name ?? "", filings: sync.data.filings_added, facts: sync.data.facts_added, total: sync.data.facts_total.toLocaleString() })}</div>}
        {syncError}
      </section>

      <div className="seg" role="group" aria-label={t("filed.mode")} style={{ alignSelf: "flex-start" }}>
        {(["standard", "filed"] as const).map((m) => (
          <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}>{t(`filed.mode.${m}`)}</button>
        ))}
      </div>

      {mode === "standard" && <StandardStatements ticker={ticker} onShowFiled={(c) => { setPicked(c); setSearch(""); setMode("filed"); }} />}

      {mode === "filed" && <div className="banner info" style={{ margin: 0 }}>{t("filed.asFiledNote")}</div>}
      {mode === "filed" && <div className="row">
        <section className="card col-side" aria-labelledby="concepts-h" style={{ gap: 8 }}>
          <h2 id="concepts-h" style={{ fontSize: "var(--sv-fs-lg)" }}>{t("filed.concepts", { n: concepts.data?.length ?? 0 })}</h2>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("filed.search")} aria-label={t("filed.search")} />
          <div role="listbox" aria-label={t("filed.concepts", { n: list.length })} style={{ maxHeight: 520, overflowY: "auto", display: "flex", flexDirection: "column" }}>
            {list.slice(0, 400).map((c: FiledConceptOut) => {
              const k = conceptKey(c);
              return (
                <button key={k} type="button" role="option" aria-selected={k === picked} onClick={() => setPicked(k)}
                        className={`concept${k === picked ? " active" : ""}`}>
                  <span>{c.label ?? c.concept}</span>
                  <span className="muted small">{c.taxonomy}:{c.concept} · {c.facts}</span>
                </button>
              );
            })}
            {list.length > 400 && <span className="muted small">{t("filed.more", { n: list.length - 400 })}</span>}
            {!list.length && <span className="muted small">{t("filed.noMatch")}</span>}
          </div>
        </section>
        <section className="card col-main" aria-labelledby="facts-h">
          {current ? <Facts ticker={ticker} concept={current} /> : <p className="muted">{t("common.loading")}</p>}
        </section>
      </div>}
    </>
  );
}

function Facts({ ticker, concept }: { ticker: string; concept: FiledConceptOut }) {
  const { t } = useI18n();
  const [annualOnly, setAnnualOnly] = useState(false);
  const facts = useQuery({ queryKey: ["filed-facts", ticker, concept.taxonomy, concept.concept],
                           queryFn: () => api.filedFacts(ticker, concept.concept, concept.taxonomy) });
  // the same period reported again with another value is a restatement: newest first, older ones flagged
  const restated = useMemo(() => {
    const seen = new Map<string, string>();
    const out = new Set<FiledFactOut>();
    for (const x of facts.data ?? []) {            // newest filing first within each period (API order)
      const k = `${x.unit}|${x.period_start ?? ""}|${x.period_end}`;
      const newest = seen.get(k);
      if (newest === undefined) seen.set(k, x.value);
      else if (newest !== x.value) out.add(x);
    }
    return out;
  }, [facts.data]);
  const rows = (facts.data ?? []).filter((x) => !annualOnly || x.form.startsWith("10-K") || x.form.startsWith("20-F") || x.form.startsWith("40-F"));
  const unit = (u: string) => (["shares", "pure", "USD/shares"].includes(u) ? t(`filed.unit.${u.replace("/", "_")}`) : u);
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h2 id="facts-h">{concept.label ?? concept.concept}</h2>
          <span className="muted small">{concept.taxonomy}:{concept.concept} · {concept.first_end} – {concept.last_end}</span>
        </div>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={annualOnly} onChange={(e) => setAnnualOnly(e.target.checked)} />{t("filed.annualOnly")}
        </label>
      </div>
      {facts.isLoading && <p className="muted">{t("common.loading")}</p>}
      {facts.error && <div role="alert" className="banner err">{(facts.error as Error).message}</div>}
      {facts.data && (
        <div className="tbl-wrap">
          <table className="tbl" style={{ minWidth: 640 }}>
            <thead><tr>
              <th scope="col" className="l">{t("filed.col.period")}</th><th scope="col">{t("filed.col.value")}</th>
              <th scope="col" className="l">{t("filed.col.filing")}</th><th scope="col">{t("filed.col.filed")}</th><th scope="col">{t("filed.col.source")}</th>
            </tr></thead>
            <tbody>
              {rows.map((x) => (
                <tr key={`${x.accession}|${x.unit}|${x.period_start}|${x.period_end}`}>
                  <td className="l">{x.period_start ? `${x.period_start} – ${x.period_end}` : t("filed.instant", { date: x.period_end })}</td>
                  <td><strong>{groupDigits(x.value)}</strong> <span className="muted small">{unit(x.unit)}</span>
                    {restated.has(x) && <> <span className="tag warn" title={t("filed.restatedHint")}>{t("filed.restated")}</span></>}</td>
                  <td className="l">{x.form}{x.fp && x.fy && <> <span className="muted small" title={t("filed.filingPeriodHint")}>{t("filed.filingPeriod", { fy: x.fy, fp: x.fp })}</span></>}</td>
                  <td>{x.filed}</td>
                  <td><a href={x.document_url ?? x.index_url} target="_blank" rel="noreferrer">SEC</a></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && <p className="muted">{t("filed.noRows")}</p>}
        </div>
      )}
    </>
  );
}
