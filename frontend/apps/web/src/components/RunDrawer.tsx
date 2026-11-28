import { ApiError, followJob, type Job, type JobStep, type Mode, type PlanTask } from "@sv/api-client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useCooldown } from "../lib/cooldown";
import { useI18n } from "../lib/i18n";
import { Close, StepIcon } from "./Icons";
import { LoadingSpinner } from "./LoadingSpinner";
import { notifyDrawerState, notifyJobStarted } from "./BackgroundJobs";

const MODES: Mode[] = ["none", "lite", "standard", "deep"];
const today = () => { const d = new Date(); const p = (x: number) => String(x).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`; };

/** Revalue drawer (design ⑦⑧): plan with per-task estimates, then live checklist with running cost. */
export function RunDrawer({ ticker, onClose }: { ticker: string; onClose: () => void }) {
  const { t, f } = useI18n();
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>("standard");
  const [picked, setPicked] = useState<Set<string> | null>(null);   // null = mode defaults
  const [date, setDate] = useState(today());
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<string | null>(null);   // same ticker/date/mode valued moments ago
  const [joined, setJoined] = useState(false);                 // following a job someone else started
  const cool = useCooldown();
  const [now, setNow] = useState(Date.now());
  const stopRef = useRef<(() => void) | null>(null);
  const tasks = picked ? [...picked] : undefined;
  const plan = useQuery({ queryKey: ["plan", ticker, mode, tasks?.join(",")], queryFn: () => api.plan(ticker, mode, tasks), enabled: !job });

  const follow = (j: Job) => {
    setJob(j);
    stopRef.current?.();
    stopRef.current = followJob(api, j.id, (next) => {
      setJob(next);
      if (["done", "failed", "cancelled"].includes(next.status)) {
        for (const k of [["valuations", ticker], ["watchlist"], ["usage"]]) void qc.invalidateQueries({ queryKey: k });
      }
    });
  };

  // notify persistent background jobs dock while this drawer is open
  useEffect(() => {
    notifyDrawerState(true, ticker);
    return () => notifyDrawerState(false, ticker);
  }, [ticker]);

  // resume a valuation of this ticker that is already running (e.g. after a reload)
  useEffect(() => {
    api.activeJobs().then((js) => { const j = js.find((x) => x.ticker === ticker); if (j) follow(j); }).catch(() => undefined);
    return () => stopRef.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker]);
  useEffect(() => {
    if (!job || ["done", "failed", "cancelled"].includes(job.status)) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [job]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const start = async (force = false) => {
    setError(null);
    setRecent(null);
    try {
      const j = await api.startValuation({ ticker, mode, tasks, valuation_date: date, force, plan: "auto" });
      setJoined(Boolean(j.joined));
      notifyJobStarted(j);
      follow(j);
    } catch (e) {
      if (e instanceof ApiError && e.code === "recent_valuation") { setRecent(e.message); return; }
      cool.from(e);              // 429 / 503: count down before the button works again
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };
  const toggle = (k: string, rows: PlanTask[]) => {
    const base = picked ?? new Set(rows.filter((r) => r.selected).map((r) => r.key));
    const next = new Set(base);
    if (next.has(k)) next.delete(k); else next.add(k);
    setPicked(next);
  };
  const noteText = (s: { note_code?: string | null; note?: string | null; skip_code?: string | null; skip_reason?: string | null }) => {
    const code = s.note_code ?? s.skip_code;
    if (code) { const k = t(`note.${code}`); if (!k.startsWith("note.")) return k; const k2 = t(`skip.${code}`); if (!k2.startsWith("skip.")) return k2; }
    return s.note ?? s.skip_reason ?? "";
  };
  const label = (s: JobStep) => (s.kind === "ai" ? `${s.key} ${t(`task.${s.key}`)}` : t(`step.${s.key}`));

  return (
    <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="run-title">
        <div className="drawer-h">
          <div>
            <h2 id="run-title" style={{ fontSize: "var(--sv-fs-2xl)" }}>{job ? t(statusTitle(job.status)) : t("run.title")}</h2>
            <span className="muted">{ticker}{job ? ` · ${t(`run.mode.${job.mode}`)}` : ""}</span>
          </div>
          <button className="iconbtn" type="button" onClick={onClose} aria-label={t("common.close")}><Close size={20} /></button>
        </div>

        {!job && (
          <>
            <div className="drawer-b">
              <fieldset className="modes">
                <legend className="field" style={{ marginBottom: 10 }}>{t("run.mode")}</legend>
                {MODES.map((m) => (
                  <label key={m} className={mode === m ? "on" : ""}>
                    <input type="radio" name="mode" checked={mode === m} onChange={() => { setMode(m); setPicked(null); }} />
                    <span><strong>{t(`run.mode.${m}`)}</strong><small>{t(`run.mode.${m}.desc`)}</small></span>
                  </label>
                ))}
              </fieldset>
              <label className="field">{t("run.date")}<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
              {plan.data && mode !== "none" && (
                <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                  <legend className="field" style={{ marginBottom: 4 }}>{t("run.tasks")}</legend>
                  <span className="muted small">{t("run.tasksHint")}</span>
                  {plan.data.tasks.filter((r) => r.in_mode || r.selected).map((r) => (
                    <label key={r.key} className={`task ${r.will_run ? "" : "off"}`}>
                      <input type="checkbox" checked={r.selected} disabled={!r.available || r.skip_code === "name_spec_locked" || r.skip_code === "no_ai_key"}
                             onChange={() => toggle(r.key, plan.data!.tasks)} />
                      <span className="t"><strong>{r.key} {t(`task.${r.key}`)}</strong>
                        <span className="muted small">{r.will_run ? r.detail : noteText(r)}</span></span>
                      <span className="c"><strong>{r.will_run ? f.usd(r.est_cost_usd) : "—"}</strong>
                        <span className="muted small">{f.tokens(r.est_tokens_in)} / {f.tokens(r.est_tokens_out)}</span></span>
                    </label>
                  ))}
                </fieldset>
              )}
              {plan.data && (
                <div className="sum">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                    <strong>{t("run.total")}</strong><span style={{ fontSize: 22, fontWeight: 700 }}>{f.usd(plan.data.total.cost_usd)}</span>
                  </div>
                  <span className="small">{t("run.totalDetail", { in: f.tokens(plan.data.total.tokens_in), out: f.tokens(plan.data.total.tokens_out),
                    low: f.usd(plan.data.total.cost_low_usd), high: f.usd(plan.data.total.cost_high_usd) })}</span>
                  {!plan.data.priced && mode !== "none" && <span className="small neg">{t("run.unpriced")}</span>}
                  <span className="small">{t("run.budget", { cap: f.usd(plan.data.budget_per_run_usd), spent: f.usd(plan.data.month_spent_usd), monthly: f.usd(plan.data.budget_monthly_usd) })}</span>
                  <div className="bar"><i style={{ width: `${Math.min(100, 100 * plan.data.month_spent_usd / Math.max(plan.data.budget_monthly_usd, 0.0001))}%` }} /></div>
                  {plan.data.over_budget && <span className="small neg">{t(`run.overBudget.${plan.data.over_budget_action}`)}</span>}
                  {plan.data.model && <span className="muted small">{plan.data.model} · ×{plan.data.calibration}</span>}
                </div>
              )}
              {error && <div className="banner err" role="alert">{error}</div>}
              {recent && (
                <div className="banner warn" role="alert" style={{ flexDirection: "column", alignItems: "flex-start" }}>
                  <span>{recent}</span>
                  <button className="btn secondary small" type="button" onClick={() => void start(true)}>{t("run.recentAgain")}</button>
                </div>
              )}
            </div>
            <div className="drawer-f">
              <button className="btn secondary" type="button" onClick={onClose}>{t("common.cancel")}</button>
              <button className="btn primary" type="button" onClick={() => void start()} disabled={plan.isLoading || cool.left > 0}>
                {cool.left > 0 ? t("run.cooldown", { seconds: cool.left })
                  : plan.data && plan.data.total.cost_usd > 0 ? t("run.startWithCost", { cost: f.usd(plan.data.total.cost_usd) }) : t("run.start")}
              </button>
            </div>
          </>
        )}

        {job && (
          <>
            <div className="drawer-b">
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--sv-fs-md)" }}>
                <span>{job.status === "queued"
                  ? (job.queue_position && job.queue_position > 1 ? t("run.queuePosition", { n: job.queue_position - 1 }) : t("run.queued"))
                  : t("run.elapsed", { time: f.duration(elapsed(job, now)) })}</span>
                <span>{job.progress}%</span>
              </div>
              {joined && <div className="banner info" role="status">{t("run.joined")}</div>}
              <div className="bar" role="progressbar" aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${job.progress}%` }} /></div>
              {job.status === "running" && job.activity && (
                <div className="banner info" role="status" aria-live="polite" style={{ padding: "10px 12px" }}>
                  <LoadingSpinner size="sm" label={job.activity} />
                  <span className="small" style={{ flex: 1, lineHeight: 1.5 }}>{job.activity}</span>
                </div>
              )}
              <div className="minis">
                <div><span className="muted small">{t("run.costSoFar")}</span><strong>{f.usd(job.cost_usd)}</strong></div>
                <div><span className="muted small">{t("run.estimated")}</span><strong>{f.usd((job.estimate as { cost_usd?: number }).cost_usd ?? 0)}</strong></div>
                <div><span className="muted small">{t("run.cap")}</span><strong>{f.usd(job.budget_usd)}</strong></div>
              </div>
              <ol className="check" aria-label={t("run.tasks")}>
                {job.steps.map((s) => {
                  const stepSec = s.status === "active" ? stepElapsed(s, now) : null;
                  const note = noteText(s);
                  return (
                    <li key={s.key} className={s.status === "active" ? "active" : ""} aria-current={s.status === "active" ? "step" : undefined}>
                      <StepIcon status={s.status} />
                      <span className="t"><strong style={{ color: s.status === "skipped" ? "var(--sv-ink-500)" : undefined }}>{label(s)}</strong>
                        <span className="muted small" style={{ whiteSpace: "normal", lineHeight: 1.45 }}>
                          {t(`status.${s.status}`)}
                          {stepSec != null && stepSec >= 1 ? ` (${f.duration(stepSec)})` : ""}
                          {note ? ` · ${note}` : ""}
                          {s.kind === "ai" && s.status === "done" && ((s.tokens_in ?? 0) > 0 || (s.tokens_out ?? 0) > 0) ? ` · ${f.tokens(s.tokens_in)} / ${f.tokens(s.tokens_out)}` : ""}
                        </span></span>
                      <span className="c">{s.kind === "ai" ? (s.status === "done" || s.status === "failed" ? f.usd(s.cost_usd) : s.est_cost_usd != null ? `~${f.usd(s.est_cost_usd)}` : "—") : "—"}</span>
                    </li>
                  );
                })}
              </ol>
              {job.status === "done" && job.result && (
                <div className="banner info">{t("run.doneSummary", { fair: f.money((job.result as { fair_value?: number }).fair_value, (job.result as { currency?: string }).currency),
                  cost: f.usd(job.cost_usd), est: f.usd((job.estimate as { cost_usd?: number }).cost_usd ?? 0) })}</div>
              )}
              {job.error && <div className="banner err" role="alert">{job.error}</div>}
            </div>
            <div className="drawer-f">
              {["queued", "running"].includes(job.status) ? (
                <>
                  <span className="muted small" style={{ flex: "1 1 160px" }}>{t("run.stopHint")}</span>
                  <button className="btn danger" type="button" onClick={() => api.cancelJob(job.id).then(setJob)}>{t("run.stop")}</button>
                  <button className="btn dark" type="button" onClick={() => { if (job) notifyJobStarted(job); onClose(); }}>{t("run.background")}</button>
                </>
              ) : (
                <button className="btn primary" type="button" onClick={onClose}>{job.status === "done" ? t("run.viewResult") : t("common.close")}</button>
              )}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function statusTitle(s: Job["status"]): string {
  return s === "done" ? "run.done" : s === "failed" ? "run.failed" : s === "cancelled" ? "run.cancelled" : "run.running";
}
function elapsed(job: Job, now: number): number {
  const startIso = job.started_at ?? job.created_at;
  const end = job.finished_at ? new Date(job.finished_at).getTime() : now;
  return Math.max(0, (end - new Date(startIso).getTime()) / 1000);
}
function stepElapsed(step: JobStep, now: number): number | null {
  const iso = (step as JobStep & { started_at?: string | null }).started_at;
  if (!iso) return null;
  const ms = new Date(iso).getTime();
  return Number.isFinite(ms) ? Math.max(0, (now - ms) / 1000) : null;
}
