import { followJob, type Job } from "@sv/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Close, StepIcon } from "./Icons";
import { LoadingSpinner } from "./LoadingSpinner";
import { RunDrawer } from "./RunDrawer";

const STORAGE_KEY = "sv.bgJobs";
const TERMINAL = new Set(["done", "failed", "cancelled"]);
const isTerminal = (j: Job) => TERMINAL.has(j.status);

type CompletedToast = {
  job: Job;
  dismissed: boolean;
};

type DrawerStateDetail = { open: boolean; ticker: string };

function loadStoredIds(): string[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string").slice(0, 12) : [];
  } catch {
    return [];
  }
}

function saveStoredIds(ids: string[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify([...new Set(ids)].slice(0, 12)));
  } catch {
    /* ignore */
  }
}

function announce(text: string, fallbackEl: HTMLDivElement | null) {
  const doc = document as Document & { ariaNotify?: (msg: string, opts?: { priority?: string }) => void };
  if (typeof doc.ariaNotify === "function") {
    try {
      doc.ariaNotify(text);
      return;
    } catch {
      /* fallback below */
    }
  }
  if (fallbackEl) {
    fallbackEl.textContent = "";
    window.setTimeout(() => { fallbackEl.textContent = text; }, 30);
  }
}

export function notifyJobStarted(job: Job) {
  window.dispatchEvent(new CustomEvent<Job>("sv:job-started", { detail: job }));
}

export function notifyDrawerState(open: boolean, ticker: string) {
  window.dispatchEvent(new CustomEvent<DrawerStateDetail>("sv:drawer-state", { detail: { open, ticker } }));
}

/**
 * Persistent site-wide background valuation tracker:
 * - Header status pill (always visible on every route when active or completed jobs exist)
 * - Persistent manual-popover dock/toast stack at bottom-right showing live progress, sub-step activity,
 *   and completion notifications with one-click navigation to results or live RunDrawer inspection.
 */
export function BackgroundJobs({ slot }: { slot: "header" | "dock" }) {
  // Share state cleanly across header pill and bottom-right dock via a tiny module store
  const snap = useBgJobsStore();
  if (slot === "header") return <BackgroundJobsHeaderPill snap={snap} />;
  return <BackgroundJobsDock snap={snap} />;
}

// ------------------------------------------------------------------ lightweight module store

type StoreState = {
  active: Record<string, Job>;
  completed: Record<string, CompletedToast>;
  minimized: boolean;
  inspectTicker: string | null;
  openDrawerTickers: Set<string>;
};

let storeState: StoreState = {
  active: {},
  completed: {},
  minimized: false,
  inspectTicker: null,
  openDrawerTickers: new Set(),
};

const listeners = new Set<() => void>();
const watchers = new Map<string, () => void>();
let initialized = false;
let pollTimer: number | null = null;
let sharedQc: ReturnType<typeof useQueryClient> | null = null;
let sharedAnnounce: ((j: Job) => void) | null = null;

function emit() {
  for (const fn of listeners) fn();
}

function setStore(patch: Partial<StoreState>) {
  storeState = { ...storeState, ...patch };
  emit();
}

function persistTracked() {
  saveStoredIds([...Object.keys(storeState.active), ...Object.keys(storeState.completed)]);
}

function onJobUpdate(next: Job) {
  if (isTerminal(next)) {
    const stop = watchers.get(next.id);
    if (stop) {
      stop();
      watchers.delete(next.id);
    }
    const wasActive = Boolean(storeState.active[next.id]);
    const nextActive = { ...storeState.active };
    delete nextActive[next.id];
    const prevCompleted = storeState.completed[next.id];
    const nextCompleted = {
      ...storeState.completed,
      [next.id]: { job: next, dismissed: prevCompleted?.dismissed ?? false },
    };
    setStore({ active: nextActive, completed: nextCompleted });
    persistTracked();

    if (sharedQc) {
      for (const k of [["valuations", next.ticker], ["methods", next.ticker], ["watchlist"], ["usage"]]) {
        void sharedQc.invalidateQueries({ queryKey: k });
      }
    }
    if (wasActive && sharedAnnounce) {
      sharedAnnounce(next);
    }
  } else {
    setStore({ active: { ...storeState.active, [next.id]: next } });
    persistTracked();
  }
}

function trackJob(job: Job) {
  if (isTerminal(job)) {
    onJobUpdate(job);
    return;
  }
  setStore({ active: { ...storeState.active, [job.id]: job } });
  persistTracked();
  if (watchers.has(job.id)) return;
  const stop = followJob(api, job.id, onJobUpdate);
  watchers.set(job.id, stop);
}

async function syncFromServer() {
  try {
    const actives = await api.activeJobs();
    for (const j of actives) trackJob(j);
    // Reconcile any previously tracked active jobs that disappeared from /jobs (finished while away)
    const activeIds = new Set(actives.map((j) => j.id));
    for (const id of [...Object.keys(storeState.active), ...loadStoredIds()]) {
      if (activeIds.has(id) || storeState.completed[id]) continue;
      try {
        const j = await api.job(id);
        onJobUpdate(j);
      } catch {
        // Job expired from server memory
        if (storeState.active[id]) {
          const nextActive = { ...storeState.active };
          delete nextActive[id];
          setStore({ active: nextActive });
        }
      }
    }
  } catch {
    /* unauthenticated or offline; retry on next poll */
  }
}

function ensureStarted() {
  if (initialized) return;
  initialized = true;
  void syncFromServer();

  const onStarted = (e: Event) => {
    const job = (e as CustomEvent<Job>).detail;
    if (job?.id) {
      setStore({ minimized: false });
      trackJob(job);
    }
  };
  const onDrawer = (e: Event) => {
    const { open, ticker } = (e as CustomEvent<DrawerStateDetail>).detail;
    const next = new Set(storeState.openDrawerTickers);
    if (open) next.add(ticker);
    else next.delete(ticker);
    setStore({ openDrawerTickers: next });
  };
  window.addEventListener("sv:job-started", onStarted);
  window.addEventListener("sv:drawer-state", onDrawer);

  pollTimer = window.setInterval(() => {
    if (document.visibilityState === "visible") void syncFromServer();
  }, 6000);
  void pollTimer;
}

function useBgJobsStore() {
  const qc = useQueryClient();
  const [, force] = useState(0);
  sharedQc = qc;

  useEffect(() => {
    ensureStarted();
    const cb = () => force((n) => n + 1);
    listeners.add(cb);
    return () => { listeners.delete(cb); };
  }, []);

  return storeState;
}

// ------------------------------------------------------------------ Header Pill

function BackgroundJobsHeaderPill({ snap }: { snap: StoreState }) {
  const { t } = useI18n();
  const activeList = Object.values(snap.active);
  const unreadDone = Object.values(snap.completed).filter((x) => !x.dismissed);
  if (!activeList.length && !unreadDone.length) return null;

  const isRunning = activeList.length > 0;
  const label = isRunning
    ? t("bg.pillRunning", { n: activeList.length, pct: activeList[0].progress ?? 0 })
    : t("bg.pillDone", { n: unreadDone.length });

  return (
    <button
      type="button"
      className={`chip-link bg-pill ${isRunning ? "running" : "done"}`}
      aria-expanded={!snap.minimized}
      onClick={() => setStore({ minimized: !snap.minimized })}
      title={t("bg.toggleHint")}
    >
      {isRunning ? <LoadingSpinner size="sm" label={label} /> : <span className="dot ok" />}
      <strong>{label}</strong>
    </button>
  );
}

// ------------------------------------------------------------------ Persistent Bottom-Right Dock & Popover Toasts

function BackgroundJobsDock({ snap }: { snap: StoreState }) {
  const { t, f } = useI18n();
  const nav = useNavigate();
  const [now, setNow] = useState(Date.now());
  const liveRef = useRef<HTMLDivElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const priorFocusRef = useRef<HTMLElement | null>(null);

  const activeList = Object.values(snap.active).filter((j) => !snap.openDrawerTickers.has(j.ticker));
  const doneList = Object.values(snap.completed).filter(
    (x) => !x.dismissed && !snap.openDrawerTickers.has(x.job.ticker),
  );
  const visibleCount = activeList.length + doneList.length;
  const anyDrawerOpen = snap.openDrawerTickers.size > 0;
  const shouldShow = !snap.minimized && visibleCount > 0 && !anyDrawerOpen;

  // Wire screen-reader announcement callback for newly finished jobs
  useEffect(() => {
    sharedAnnounce = (j: Job) => {
      const msg = j.status === "done"
        ? t("bg.announceDone", { ticker: j.ticker })
        : j.status === "failed"
          ? t("bg.announceFailed", { ticker: j.ticker })
          : t("bg.announceCancelled", { ticker: j.ticker });
      announce(`${msg} Actions available`, liveRef.current);
    };
  }, [t]);

  // 1s live elapsed timer while any active job is visible
  useEffect(() => {
    if (!activeList.length) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [activeList.length]);

  // Manage native popover="manual" open/close without stealing focus
  useEffect(() => {
    const el = popoverRef.current as (HTMLDivElement & { showPopover?: () => void; hidePopover?: () => void }) | null;
    if (!el) return;
    if (shouldShow) {
      if (typeof el.showPopover === "function") {
        try {
          if (!el.matches(":popover-open")) el.showPopover();
        } catch {
          el.classList.add(":popover-open");
        }
      } else {
        el.classList.add(":popover-open");
      }
    } else {
      if (typeof el.hidePopover === "function") {
        try {
          if (el.matches(":popover-open")) el.hidePopover();
        } catch {
          el.classList.remove(":popover-open");
        }
      } else {
        el.classList.remove(":popover-open");
      }
    }
  }, [shouldShow]);

  const dismiss = useCallback((jobId: string, e?: React.MouseEvent<HTMLButtonElement>) => {
    const cur = storeState.completed[jobId];
    if (!cur) return;
    const nextCompleted = { ...storeState.completed, [jobId]: { ...cur, dismissed: true } };
    setStore({ completed: nextCompleted });
    persistTracked();
    // Manage focus on manual dismissal: move to next toast button or restore prior focus
    const btn = e?.currentTarget;
    if (btn && document.activeElement === btn) {
      window.setTimeout(() => {
        const nextBtn = popoverRef.current?.querySelector<HTMLButtonElement>("button, a");
        if (nextBtn) nextBtn.focus();
        else priorFocusRef.current?.focus();
      }, 0);
    }
  }, []);

  const openResult = (job: Job) => {
    dismiss(job.id);
    nav(`/stock/${encodeURIComponent(job.ticker)}/valuation`);
  };

  return (
    <>
      <p className="sr-only">{t("bg.srContext")}</p>
      <div ref={liveRef} className="sr-only" aria-live="polite" aria-atomic="true" />

      <div
        ref={popoverRef}
        {...({ popover: "manual" } as Record<string, string>)}
        role="region"
        aria-label={t("bg.region")}
        className={`bg-dock${shouldShow ? " is-open" : ""}`}
        onFocus={() => {
          if (!priorFocusRef.current && document.activeElement instanceof HTMLElement && !popoverRef.current?.contains(document.activeElement)) {
            priorFocusRef.current = document.activeElement;
          }
        }}
      >
        {activeList.map((j) => {
          const sec = elapsedSec(j, now);
          return (
            <article key={j.id} className="bg-toast active" aria-live="polite">
              <div className="bg-toast-h">
                <div className="loading-row" style={{ gap: 8, minWidth: 0 }}>
                  <LoadingSpinner size="sm" label={t("bg.runningLabel", { ticker: j.ticker })} />
                  <strong>{j.ticker}</strong>
                  <span className="tag primary">{j.progress}%</span>
                  <span className="muted small">{f.duration(sec)}</span>
                </div>
                <button
                  type="button"
                  className="iconbtn bg-close"
                  aria-label={t("bg.minimize")}
                  title={t("bg.minimize")}
                  onClick={() => setStore({ minimized: true })}
                >
                  <Close size={16} />
                </button>
              </div>
              <div className="bar" role="progressbar" aria-valuenow={j.progress} aria-valuemin={0} aria-valuemax={100}>
                <i style={{ width: `${j.progress}%` }} />
              </div>
              <div className="muted small bg-toast-body">
                {j.activity || t("run.running")}
              </div>
              <div className="bg-toast-actions">
                <button
                  type="button"
                  className="btn secondary small"
                  onClick={() => setStore({ inspectTicker: j.ticker })}
                >
                  {t("bg.inspect")}
                </button>
                <button
                  type="button"
                  className="btn secondary small"
                  onClick={() => nav(`/stock/${encodeURIComponent(j.ticker)}`)}
                >
                  {t("bg.openStock")}
                </button>
              </div>
            </article>
          );
        })}

        {doneList.map(({ job: j }) => {
          const res = j.result as { fair_value?: number | null; currency?: string | null } | null | undefined;
          const isOk = j.status === "done";
          const isFail = j.status === "failed";
          return (
            <article key={j.id} className={`bg-toast ${isOk ? "done" : isFail ? "err" : "muted-card"}`}>
              <div className="bg-toast-h">
                <div className="loading-row" style={{ gap: 8, minWidth: 0 }}>
                  <StepIcon status={j.status === "done" ? "done" : j.status === "failed" ? "failed" : "cancelled"} />
                  <strong>{j.ticker}</strong>
                  <span className={`tag ${isOk ? "ok" : isFail ? "bad" : "neutral"}`}>
                    {t(isOk ? "run.done" : isFail ? "run.failed" : "run.cancelled")}
                  </span>
                </div>
                <button
                  type="button"
                  className="iconbtn bg-close"
                  aria-label={t("common.close")}
                  onClick={(e) => dismiss(j.id, e)}
                >
                  <Close size={16} />
                </button>
              </div>
              <div className="small bg-toast-body">
                {isOk && res?.fair_value != null
                  ? t("bg.doneSummary", { fair: f.money(res.fair_value, res.currency ?? undefined), cost: f.usd(j.cost_usd) })
                  : isFail
                    ? (j.error || t("run.failed"))
                    : t("run.cancelled")}
              </div>
              <div className="bg-toast-actions">
                <button type="button" className="btn primary small" onClick={() => openResult(j)}>
                  {t("run.viewResult")}
                </button>
                <button type="button" className="btn secondary small" onClick={(e) => dismiss(j.id, e)}>
                  {t("common.close")}
                </button>
              </div>
            </article>
          );
        })}
      </div>

      {snap.inspectTicker && (
        <RunDrawer
          ticker={snap.inspectTicker}
          onClose={() => setStore({ inspectTicker: null })}
        />
      )}
    </>
  );
}

function elapsedSec(job: Job, now: number): number {
  const startIso = job.started_at ?? job.created_at;
  const end = job.finished_at ? new Date(job.finished_at).getTime() : now;
  return Math.max(0, (end - new Date(startIso).getTime()) / 1000);
}
