import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";

const RISK_TAG: Record<string, string> = { high: "bad", medium: "warn", low: "ok" };

/** Displays persisted T1-T8 AI task outputs for a valuation run on the Valuation tab. */
export function RunAiAnalysisCard({ runId, currency }: { runId: string; currency?: string | null }) {
  const { t, f } = useI18n();
  const q = useQuery({ queryKey: ["run-analysis", runId], queryFn: () => api.runAnalysis(runId), enabled: !!runId });
  const a = (q.data?.ai_analysis ?? {}) as Record<string, Record<string, unknown>>;
  const keys = Object.keys(a);
  if (!keys.length) return null;

  const t4 = a.T4 as { probability_weighted_fv?: number; scenarios?: { key: string; fair_value: number; probability: number; trigger: string; narrative: string }[] } | undefined;
  const t5 = a.T5 as { peers?: { ticker: string; rationale: string }[] } | undefined;
  const t6 = a.T6 as { risk_level?: string; findings?: { severity: string; topic: string; detail: string }[] } | undefined;
  const t7 = a.T7 as { thesis?: string; risks?: string[]; attribution?: { from_date?: string; to_date?: string; from_value?: number; to_value?: number; delta?: number; drivers?: string[] } | null } | undefined;
  const t8 = a.T8 as { segments?: { name: string; revenue_share?: number | null; driver: string }[]; sotp_note?: string } | undefined;

  return (
    <section className="card" aria-labelledby="ai-analysis-h" style={{ gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <h2 id="ai-analysis-h" style={{ margin: 0 }}>{t("aiAnalysis.title")}</h2>
          <span className="muted small">{t("aiAnalysis.subtitle", { tasks: keys.join(" · ") })}</span>
        </div>
      </div>

      {t7 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--sv-line-faint)", paddingTop: 10 }}>
          <strong>T7 {t("task.T7")}</strong>
          {t7.thesis && <p style={{ margin: 0, lineHeight: 1.6 }}>{t7.thesis}</p>}
          {t7.attribution && (
            <div className="banner info" style={{ flexDirection: "column", alignItems: "flex-start", gap: 4 }}>
              <strong>{t("aiAnalysis.attribution")}: {f.money(t7.attribution.from_value, currency)} → {f.money(t7.attribution.to_value, currency)} ({f.delta(t7.attribution.delta)})</strong>
              <span className="small">{(t7.attribution.drivers ?? []).join(" · ")}</span>
            </div>
          )}
        </div>
      )}

      {t6 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--sv-line-faint)", paddingTop: 10 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <strong>T6 {t("task.T6")}</strong>
            {t6.risk_level && <span className={`tag ${RISK_TAG[t6.risk_level] ?? "neutral"}`}>{t(`aiAnalysis.risk.${t6.risk_level}`)}</span>}
          </div>
          {(t6.findings ?? []).length === 0 ? <span className="muted small">{t("aiAnalysis.noFindings")}</span> : (
            <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4 }}>
              {(t6.findings ?? []).map((x, i) => (
                <li key={i} className="small">
                  <span className={`tag ${RISK_TAG[x.severity] ?? "neutral"}`}>{x.severity.toUpperCase()}</span>{" "}
                  {x.detail}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {t4 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--sv-line-faint)", paddingTop: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <strong>T4 {t("task.T4")}</strong>
            {t4.probability_weighted_fv != null && (
              <span className="small">{t("aiAnalysis.pwFv")}: <strong>{f.money(t4.probability_weighted_fv, currency)}</strong></span>
            )}
          </div>
          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 520 }}>
              <thead><tr>
                <th scope="col" className="l">{t("aiAnalysis.scenario")}</th>
                <th scope="col">{t("aiAnalysis.prob")}</th>
                <th scope="col">{t("aiAnalysis.fv")}</th>
                <th scope="col" className="l">{t("aiAnalysis.trigger")}</th>
              </tr></thead>
              <tbody>
                {(t4.scenarios ?? []).map((s) => (
                  <tr key={s.key}>
                    <th scope="row" className="l">{t(`stock.scenario.${s.key}`)}</th>
                    <td>{f.pct(s.probability, 0, false)}</td>
                    <td><strong>{f.money(s.fair_value, currency)}</strong></td>
                    <td className="l" style={{ whiteSpace: "normal" }}>{s.trigger}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {t5 && (t5.peers?.length ?? 0) > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--sv-line-faint)", paddingTop: 10 }}>
          <strong>T5 {t("task.T5")}</strong>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {(t5.peers ?? []).map((p) => (
              <span key={p.ticker} className="chip" title={p.rationale}><strong>{p.ticker}</strong></span>
            ))}
          </div>
        </div>
      )}

      {t8 && (t8.segments?.length ?? 0) > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--sv-line-faint)", paddingTop: 10 }}>
          <strong>T8 {t("task.T8")}</strong>
          <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4 }}>
            {(t8.segments ?? []).map((s, i) => (
              <li key={i} className="small">
                <strong>{s.name}</strong>{s.revenue_share != null ? ` (${f.pct(s.revenue_share, 0, false)})` : ""}: {s.driver}
              </li>
            ))}
          </ul>
          {t8.sotp_note && <span className="muted small">{t8.sotp_note}</span>}
        </div>
      )}
    </section>
  );
}
