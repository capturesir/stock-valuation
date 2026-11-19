import type { AdjustmentIn, AdjustmentOut, AdjustmentPlanOut, AdjustmentPlansOut, StdRow } from "@sv/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { useIsAdmin } from "../lib/session";
import { groupDigits } from "./FiledData";

/** Move the decimal point of an exact decimal string by n places (no floating point): "1.5", 6 → "1500000". */
export function shiftDecimal(s: string, n: number): string {
  const m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(s.trim().replace(/,/g, ""));
  if (!m || (!m[2] && !m[3])) return "";
  let [int, frac] = [m[2] || "0", m[3] || ""];
  if (n >= 0) { frac = frac.padEnd(n, "0"); int += frac.slice(0, n); frac = frac.slice(n); }
  else { int = int.padStart(-n + 1, "0"); frac = int.slice(n) + frac; int = int.slice(0, n); }
  int = int.replace(/^0+(?=\d)/, ""); frac = frac.replace(/0+$/, "");
  const out = frac ? `${int}.${frac}` : int;
  return m[1] && /[1-9]/.test(out) ? `-${out}` : out;
}

const toIn = (a: AdjustmentOut): AdjustmentIn => ({ item: a.item, period: a.period, value: a.value, reason: a.reason, basis: a.basis ?? null });
const keyOf = (a: { item: string; period: string }) => `${a.item}|${a.period}`;
const same = (a: AdjustmentOut | undefined, b: AdjustmentOut | undefined) =>
  !!a && !!b && a.value === b.value && a.reason === b.reason && (a.basis ?? "") === (b.basis ?? "");

export function usePlans(ticker: string) {
  return useQuery({ queryKey: ["plans", ticker], queryFn: () => api.adjustmentPlans(ticker) });
}

function useSave(ticker: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, body }: { kind: "central" | "personal"; body: Parameters<typeof api.saveAdjustmentPlan>[2] }) =>
      api.saveAdjustmentPlan(ticker, kind, body),
    onSuccess: (data) => { qc.setQueryData(["plans", ticker], data); qc.invalidateQueries({ queryKey: ["std", ticker] }); },
  });
}

/** The adjustments a plan of this kind would start from: its own current set, or (a new personal plan) the central one. */
function startingSet(plans: AdjustmentPlansOut, kind: "central" | "personal") {
  const own = kind === "central" ? plans.central : plans.personal;
  if (own) return { base: own.current_version, list: own.current.adjustments.map(toIn), central: undefined as number | undefined };
  const c = plans.central;
  return { base: 0, list: kind === "personal" && c ? c.current.adjustments.map(toIn) : [], central: kind === "personal" ? c?.current_version : undefined };
}

/** Editor for one cell (opened from the lineage panel): value in the table's unit, reason required, optional basis. */
export function AdjustCell({ ticker, row, period, current, onDone }: {
  ticker: string; row: StdRow; period: string; current: string | null; onDone: () => void;
}) {
  const { t } = useI18n();
  const isAdmin = useIsAdmin();
  const plans = usePlans(ticker);
  const save = useSave(ticker);
  const scaled = row.unit !== "per_share";                         // money and share counts are shown in millions
  const [kind, setKind] = useState<"central" | "personal">("personal");
  const [value, setValue] = useState(current != null ? (scaled ? shiftDecimal(current, -6) : current) : "");
  const [reason, setReason] = useState("");
  const [basis, setBasis] = useState("");
  if (!plans.data) return null;
  const start = startingSet(plans.data, kind);
  const existing = start.list.find((a) => a.item === row.code && a.period === period);
  const submit = (remove: boolean) => {
    const others = start.list.filter((a) => !(a.item === row.code && a.period === period));
    const exact = scaled ? shiftDecimal(value, 6) : shiftDecimal(value, 0);
    const list = remove ? others : [...others, { item: row.code, period, value: exact, reason: reason.trim(), basis: basis.trim() || null }];
    save.mutate({ kind, body: { adjustments: list, base_version: start.base, central_version: start.central } }, { onSuccess: onDone });
  };
  const valid = !!shiftDecimal(value, 0) && reason.trim().length > 0;
  return (
    <form className="adjust" onSubmit={(e) => { e.preventDefault(); if (valid) submit(false); }}
          style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
      <strong>{t("adj.editTitle")}</strong>
      {isAdmin && (
        <div className="seg" role="group" aria-label={t("adj.target")}>
          {(["personal", "central"] as const).map((k) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>{t(`adj.kind.${k}`)}</button>
          ))}
        </div>
      )}
      {kind === "personal" && !plans.data.personal && <span className="small muted">{t("adj.firstPersonal")}</span>}
      <label>{t(scaled ? "adj.valueMillions" : "adj.value")}
        <input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} required /></label>
      <label>{t("adj.reason")}<input value={reason} onChange={(e) => setReason(e.target.value)} required maxLength={2000} /></label>
      <label>{t("adj.basis")}<input value={basis} onChange={(e) => setBasis(e.target.value)} maxLength={2000} placeholder={t("adj.basisHint")} /></label>
      {save.error && <div role="alert" className="banner err">{(save.error as Error).message}</div>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="submit" className="btn small" disabled={!valid || save.isPending}>{t("adj.save")}</button>
        {existing && <button type="button" className="btn secondary small" disabled={save.isPending} onClick={() => submit(true)}>{t("adj.remove")}</button>}
        <button type="button" className="btn secondary small" onClick={onDone}>{t("common.cancel")}</button>
      </div>
    </form>
  );
}

/** Both plans for a stock: current adjustments, every version, a two-version comparison, and merging central updates. */
export function AdjustmentPlans({ ticker }: { ticker: string }) {
  const { t } = useI18n();
  const plans = usePlans(ticker);
  const p = plans.data;
  if (!p) return null;
  return (
    <div style={{ borderTop: "1px solid var(--sv-line-faint)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
      <h3 style={{ margin: 0 }}>{t("adj.title")}</h3>
      <span className="muted small">{t("adj.intro")}</span>
      {p.central_updated && p.central && p.personal && <MergeCentral ticker={ticker} central={p.central} personal={p.personal} />}
      {(["central", "personal"] as const).map((k) => {
        const plan = k === "central" ? p.central : p.personal;
        return plan ? <Plan key={k} plan={plan} /> : <span key={k} className="small muted">{t(`adj.none.${k}`)}</span>;
      })}
    </div>
  );
}

function AdjTable({ list, mark }: { list: AdjustmentOut[]; mark?: Record<string, string> }) {
  const { t } = useI18n();
  if (!list.length) return <span className="small muted">{t("adj.empty")}</span>;
  return (
    <div className="tbl-wrap">
      <table className="tbl" style={{ minWidth: 720 }}>
        <thead><tr><th scope="col" className="l">{t("adj.col.item")}</th><th scope="col" className="l">{t("adj.col.period")}</th>
          <th scope="col">{t("adj.col.original")}</th><th scope="col">{t("adj.col.value")}</th>
          <th scope="col" className="l">{t("adj.col.reason")}</th><th scope="col" className="l">{t("adj.col.by")}</th></tr></thead>
        <tbody>
          {list.map((a) => (
            <tr key={keyOf(a)}>
              <td className="l">{mark?.[keyOf(a)] && <span className="tag neutral">{t(`adj.diff.${mark[keyOf(a)]}`)}</span>} {t(`std.item.${a.item}`)}</td>
              <td className="l">{a.period}</td>
              <td>{a.original_value != null ? groupDigits(a.original_value) : "—"}</td>
              <td>{groupDigits(a.value)}</td>
              <td className="l" style={{ whiteSpace: "normal" }}>{a.reason}{a.basis && <span className="muted small"> · {a.basis}</span>}</td>
              <td className="l small">{a.adjusted_by ?? "—"} · {a.adjusted_at.slice(0, 10)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Plan({ plan }: { plan: AdjustmentPlanOut }) {
  const { t } = useI18n();
  const [a, setA] = useState<number | null>(null);
  const [b, setB] = useState<number | null>(null);
  const va = useQuery({ queryKey: ["planv", plan.id, a], queryFn: () => api.adjustmentVersion(plan.id, a!), enabled: a != null });
  const vb = useQuery({ queryKey: ["planv", plan.id, b], queryFn: () => api.adjustmentVersion(plan.id, b!), enabled: b != null });
  const diff = (() => {
    if (!va.data || !vb.data) return null;
    const A = new Map(va.data.adjustments.map((x) => [keyOf(x), x])), B = new Map(vb.data.adjustments.map((x) => [keyOf(x), x]));
    const mark: Record<string, string> = {};
    const list: AdjustmentOut[] = [];
    for (const [k, x] of B) if (!A.has(k)) { mark[k] = "added"; list.push(x); } else if (!same(A.get(k), x)) { mark[k] = "changed"; list.push(x); }
    for (const [k, x] of A) if (!B.has(k)) { mark[k] = "removed"; list.push(x); }
    return { mark, list };
  })();
  const opts = plan.versions.map((v) => v.version);
  return (
    <details open={plan.kind === "personal"} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <summary><strong>{t(`adj.kind.${plan.kind}`)} v{plan.current_version}</strong>
        <span className="muted small"> · {plan.current.created_by_name ?? "—"} · {plan.current.created_at?.slice(0, 10)}
          {plan.kind === "personal" && plan.source_version != null && <> · {t("adj.basedOn", { v: plan.source_version })}</>}</span></summary>
      {plan.current.note && <span className="small">{plan.current.note}</span>}
      <AdjTable list={plan.current.adjustments} />
      <span className="small"><strong>{t("adj.versions")}</strong>：{plan.versions.map((v) =>
        `v${v.version}（${v.created_by_name ?? "—"}，${v.created_at.slice(0, 10)}，${t("adj.count", { n: v.adjustments })}${v.note ? `，${v.note}` : ""}）`).join("；")}</span>
      {opts.length > 1 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span className="small">{t("adj.compare")}</span>
          {[[a, setA], [b, setB]].map(([v, set], i) => (
            <select key={i} aria-label={t("adj.compare")} value={(v as number | null) ?? ""}
                    onChange={(e) => (set as (n: number | null) => void)(e.target.value ? Number(e.target.value) : null)}>
              <option value="">—</option>
              {opts.map((n) => <option key={n} value={n}>v{n}</option>)}
            </select>
          ))}
        </div>
      )}
      {diff && (diff.list.length ? <AdjTable list={diff.list} mark={diff.mark} /> : <span className="small muted">{t("adj.noDiff")}</span>)}
    </details>
  );
}

function MergeCentral({ ticker, central, personal }: { ticker: string; central: AdjustmentPlanOut; personal: AdjustmentPlanOut }) {
  const { t } = useI18n();
  const save = useSave(ticker);
  const mine = new Map(personal.current.adjustments.map((x) => [keyOf(x), x]));
  const offered = central.current.adjustments.filter((x) => !same(mine.get(keyOf(x)), x));
  const [picked, setPicked] = useState<Set<string>>(new Set(offered.map(keyOf)));
  const apply = () => {
    const take = offered.filter((x) => picked.has(keyOf(x)));
    const list = [...personal.current.adjustments.filter((x) => !take.some((y) => keyOf(y) === keyOf(x))), ...take].map(toIn);
    save.mutate({ kind: "personal", body: { adjustments: list, base_version: personal.current_version, central_version: central.current_version,
                                            note: t("adj.mergeNote", { v: central.current_version }) } });
  };
  return (
    <div className="banner warn" role="region" aria-label={t("adj.centralUpdated")} style={{ flexDirection: "column", alignItems: "flex-start", gap: 6 }}>
      <strong>{t("adj.centralUpdated")}</strong>
      <span className="small">{t("adj.mergeIntro", { from: personal.source_version ?? 0, to: central.current_version })}</span>
      {offered.map((x) => (
        <label key={keyOf(x)} className="small" style={{ display: "flex", gap: 6 }}>
          <input type="checkbox" checked={picked.has(keyOf(x))}
                 onChange={(e) => { const s = new Set(picked); if (e.target.checked) s.add(keyOf(x)); else s.delete(keyOf(x)); setPicked(s); }} />
          {t(`std.item.${x.item}`)} {x.period}：{groupDigits(x.value)}（{x.reason}）
          {mine.get(keyOf(x)) && <span className="muted">· {t("adj.mineIs", { v: groupDigits(mine.get(keyOf(x))!.value) })}</span>}
        </label>
      ))}
      {save.error && <div role="alert" className="banner err">{(save.error as Error).message}</div>}
      <button type="button" className="btn small" onClick={apply} disabled={save.isPending}>
        {offered.length ? t("adj.merge", { n: picked.size }) : t("adj.markReviewed")}</button>
    </div>
  );
}
