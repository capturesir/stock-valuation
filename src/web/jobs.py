"""Background valuation jobs: one run at a time, pollable status."""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

from ..pipeline.orchestrator import ROOT, ValuationCancelled, run_ticker
from .services import norm_ticker, validate_date

# The last few runs (status, failing step, message) survive a restart so the
# 「錯誤」panel can show what happened previously. Local file, gitignored.
HISTORY_PATH = ROOT / "out" / "_run_history.json"
HISTORY_MAX = 3


class JobBusy(Exception):
    def __init__(self, current: dict):
        self.current = current
        ticker = current.get("ticker") or ""
        super().__init__(f"已有估值工作進行中（{ticker}）。請待其完成後再試。")


# The valuation pipeline, in the order orchestrator.run_ticker performs it.
# The web UI renders exactly this list, so keep the keys in sync with the
# progress() calls in src/pipeline/orchestrator.py.
STEPS = [
    ("quote", "取得市價"),
    ("fundamentals", "讀取財報與分析師預測"),
    ("rf", "取得無風險利率"),
    ("filings", "讀取最新業績公告"),
    ("ai_extract", "AI 讀取財報摘要"),
    ("ai_assume", "AI 提出增長與利潤率假設"),
    ("spec", "建立模型規格"),
    ("engine", "計算五情境並驗證"),
    ("export", "產出 Excel 與說明"),
    ("state", "寫入執行紀錄"),
]
STEP_LABELS = dict(STEPS)


def _new_steps() -> list[dict]:
    return [{"key": k, "label": lbl, "status": "pending"} for k, lbl in STEPS]


def _active_label(job: dict) -> str:
    for s in job.get("steps") or []:
        if s.get("status") == "error":
            return s.get("label") or s.get("key") or "未知"
    return "未知"


class JobStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.active_id: Optional[str] = None

    def reset(self) -> None:
        with self._lock:
            self.jobs.clear()
            self.active_id = None

    def get(self, job_id: str) -> Optional[dict]:
        return self.jobs.get(job_id)

    def active(self) -> Optional[dict]:
        if not self.active_id:
            return None
        job = self.jobs.get(self.active_id)
        if job and job.get("status") in ("running", "queued", "cancelling"):
            return job
        return None

    def public(self, job: dict) -> dict:
        out = {k: job[k] for k in (
            "id", "kind", "ticker", "valuation_date", "use_llm", "status",
            "message", "error", "result", "started_at", "finished_at",
            "steps", "progress", "activity", "phase",
        ) if k in job}
        out["cancellable"] = job.get("status") in ("running", "queued", "cancelling")
        return out

    def cancel(self, job_id: str) -> Optional[dict]:
        """Ask a running valuation to stop. Cooperative: the current step ends first."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job.get("status") in ("running", "queued"):
                job["cancel"].set()
                job["status"] = "cancelling"
                job["activity"] = "正在停止…（目前這一步完成後停止）"
                job["phase"] = "stopping"
            return job


    def start_valuation(self, ticker: str, valuation_date: Optional[str] = None,
                        use_llm: bool = True) -> dict:
        ticker = norm_ticker(ticker)
        date = validate_date(valuation_date)
        if not ticker:
            raise ValueError("請輸入股票代號。")
        with self._lock:
            current = self.active()
            if current:
                raise JobBusy(current)
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "kind": "valuate",
                "ticker": ticker,
                "valuation_date": date,
                "use_llm": use_llm,
                "status": "running",
                "message": "正在抓取市價、財報與假設，並計算五情境（可能需要數分鐘）…",
                "steps": _new_steps(),
                "progress": 0,
                "activity": "準備開始…",
                "phase": "fetch",
                "cancel": threading.Event(),
                "error": None,
                "result": None,
                "started_at": time.time(),
                "finished_at": None,
            }
            self.jobs[job_id] = job
            self.active_id = job_id
            thread = threading.Thread(target=self._run, args=(job,), daemon=True)
            thread.start()
            return job

    def _run(self, job: dict) -> None:
        lock = self._lock

        def progress(key: str, activity: Optional[str] = None, skip: Optional[str] = None) -> None:
            """Mark a pipeline step active (or skipped) and publish a one-line activity.

            Purely local: the text is written by orchestrator, so showing it costs
            no model tokens.
            """
            with lock:
                steps = job.get("steps") or []
                idx = next((i for i, s in enumerate(steps) if s["key"] == key), None)
                if idx is None:
                    return
                for i, s in enumerate(steps):
                    if i < idx and s["status"] in ("pending", "active"):
                        s["status"] = "done"
                if skip:
                    steps[idx]["status"] = "skipped"
                    steps[idx]["note"] = skip
                    activity = activity or skip
                else:
                    steps[idx]["status"] = "active"
                if activity:
                    job["activity"] = activity
                job["phase"] = ("ai" if key.startswith("ai_")
                                else "calc" if key in ("spec", "engine") else "fetch")
                done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
                job["progress"] = int(round(100 * done / max(1, len(steps))))

        try:
            kwargs = {"ticker": job["ticker"], "use_llm": bool(job.get("use_llm", True)),
                      "progress": progress, "should_stop": job["cancel"].is_set}
            if job.get("valuation_date"):
                kwargs["valuation_date"] = job["valuation_date"]
            result = run_ticker(**kwargs)
            js = result.get("json") or {}

            job["result"] = {
                "ticker": js.get("ticker") or job["ticker"],
                "asset": js.get("asset"),
                "price": js.get("price"),
                "price_date": js.get("price_date"),
                "currency": js.get("currency"),
                "valuation_date": js.get("valuation_date") or job.get("valuation_date"),
                "fair_value_per_share": js.get("fair_value_per_share"),
                "price_over_fair": (js.get("gap_vs_price") or {}).get("price_over_fair_ratio"),
                "wacc": ((js.get("discount") or {}).get("wacc") or {}).get("value"),
                "g": (js.get("terminal") or {}).get("g"),
                "scenarios": [
                    {"name": s.get("name"), "fair_value": s.get("fair_value")}
                    for s in (js.get("scenarios") or [])
                ],
                "verify_pass": result.get("verify_pass"),
                "verify_errors": result.get("verify_errors") or [],
                "assumption_source": js.get("assumption_source"),
                "xlsx": result.get("xlsx"),
                "md": result.get("md"),
                "json": result.get("path"),
                "out_dir": result.get("out_dir"),
            }
            passed = bool(result.get("verify_pass"))
            with lock:
                for s in job.get("steps") or []:
                    if s["status"] in ("pending", "active"):
                        s["status"] = "done"
                job["progress"] = 100
                job["activity"] = "完成"
                job["phase"] = "done"
            job["status"] = "done"
            job["message"] = "估值完成（驗證通過）" if passed else "估值完成，但獨立驗證未通過"
        except ValuationCancelled:
            with lock:
                for s in job.get("steps") or []:
                    if s["status"] == "active":
                        s["status"] = "cancelled"
                        s["note"] = "用戶停止"
                job["phase"] = "cancelled"
                job["activity"] = "已停止"
                job["progress"] = job.get("progress") or 0
            job["status"] = "cancelled"
            job["message"] = "已停止（未產生估值）"
            job["error"] = None
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            with lock:
                for s in job.get("steps") or []:
                    if s["status"] == "active":
                        s["status"] = "error"
                        s["error"] = message          # shown inline next to the step
                job["phase"] = "error"
                job["activity"] = f"失敗於「{_active_label(job)}」：{message}"
            job["status"] = "error"
            job["error"] = message
            job["error_detail"] = traceback.format_exc()
            job["message"] = "估值失敗"

        finally:
            job["finished_at"] = time.time()
            _save_history(job)


def _history_entry(job: dict) -> dict:
    steps = job.get("steps") or []
    failed = next((s for s in steps if s.get("status") == "error"), None)
    stopped = next((s for s in steps if s.get("status") == "cancelled"), None)
    return {
        "id": job.get("id"),
        "ticker": job.get("ticker"),
        "valuation_date": job.get("valuation_date"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "status": job.get("status"),
        "message": job.get("message"),
        "error": job.get("error"),
        "error_step": (failed or stopped or {}).get("label"),
        "stopped": bool(stopped and not failed),
        "steps": [
            {k: s.get(k) for k in ("key", "label", "status", "note", "error") if s.get(k)}
            for s in steps
        ],
    }


def _save_history(job: dict) -> None:
    """Keep the newest HISTORY_MAX runs in out/_run_history.json (never fatal)."""
    try:
        runs = load_history()
        runs.insert(0, _history_entry(job))
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(
            json.dumps(runs[:HISTORY_MAX], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception:
        pass


def load_history() -> list[dict]:
    try:
        data = json.loads(Path(HISTORY_PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


store = JobStore()
