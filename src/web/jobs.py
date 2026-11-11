"""Background valuation jobs: one run at a time, pollable status."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Optional

from ..pipeline.orchestrator import run_ticker
from .services import norm_ticker, validate_date


class JobBusy(Exception):
    def __init__(self, current: dict):
        self.current = current
        ticker = current.get("ticker") or ""
        super().__init__(f"已有估值工作進行中（{ticker}）。請待其完成後再試。")


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
        if job and job.get("status") in ("running", "queued"):
            return job
        return None

    def public(self, job: dict) -> dict:
        out = {k: job[k] for k in (
            "id", "kind", "ticker", "valuation_date", "use_llm", "status",
            "message", "error", "result", "started_at", "finished_at",
        ) if k in job}
        return out

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
        try:
            kwargs = {"ticker": job["ticker"], "use_llm": bool(job.get("use_llm", True))}
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
            job["status"] = "done"
            job["message"] = "估值完成（驗證通過）" if passed else "估值完成，但獨立驗證未通過"
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc) or type(exc).__name__
            job["error_detail"] = traceback.format_exc()
            job["message"] = "估值失敗"
        finally:
            job["finished_at"] = time.time()


store = JobStore()
