"""Run one valuation through the v1 engine and record it: progress, AI cost, results."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import traceback
from typing import Optional

from sqlalchemy.orm import Session

from .. import pricing
from ..db import session
from ..jobs import now_iso, store
from ..models import AiTask, MethodResult, Run
from . import settings_store

# v1 orchestrator step keys → checklist labels (ai_extract is skipped in v2, see orchestrator)
DATA_STEPS = [("quote", "取得市價"), ("fundamentals", "讀取財報與分析師預測"),
              ("rf", "取得無風險利率"), ("filings", "讀取最新公告")]
LATE_STEPS = [("spec", "建立模型規格（行業 β、折現率、橋接）"), ("engine", "引擎計算五情境並獨立驗證"),
              ("export", "產出 Excel 與說明"), ("state", "寫入估值紀錄")]
# where each AI task runs inside the v1 pipeline
AI_HOOKS = {"ai_assume": "T3"}
SCENARIOS = {"悲觀": "bear", "基準": "base", "樂觀": "bull", "靜態": "static", "9%靜態": "floor9"}


def build_steps(plan_tasks: list[dict]) -> list[dict]:
    steps = [{"key": k, "label": l, "kind": "data", "status": "pending", "note": None} for k, l in DATA_STEPS]
    for t in plan_tasks:
        if not (t["in_mode"] or t["selected"]):
            continue
        steps.append({
            "key": t["key"], "label": f"{t['key']} {t['label']}", "kind": "ai",
            "status": "pending" if t["will_run"] else "skipped",
            "note": None if t["will_run"] else t["skip_reason"],
            "note_code": None if t["will_run"] else t.get("skip_code"),
            "est_cost_usd": t["est_cost_usd"] if t["will_run"] else None,
            "est_tokens_in": t["est_tokens_in"], "est_tokens_out": t["est_tokens_out"],
            "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
        })
    steps += [{"key": k, "label": l, "kind": "engine", "status": "pending", "note": None} for k, l in LATE_STEPS]
    return steps


def _progress(steps: list[dict]) -> int:
    live = [s for s in steps if s["status"] != "skipped"]
    done = sum(1 for s in live if s["status"] in ("done", "cached", "failed"))
    return int(round(100 * done / max(1, len(live))))


def trading_ccy(calc: dict) -> Optional[str]:
    pair = ((calc.get("fx_if_any") or {}).get("pair") or "")
    return pair.split("/")[1] if "/" in pair else calc.get("currency")


def archive_outputs(job_id: str, result: dict) -> tuple[Optional[str], Optional[str]]:
    """Copy this run's files into <APP_DATA_DIR>/runs/<job_id>/ and return the archived paths.

    The v1 engine writes to out/<date>_<ticker>/, so a second run of the same ticker on the same day
    overwrote the first run's Excel and JSON while both database records still pointed there. Each
    run now keeps its own copy; out/ stays as the v1 tools expect it."""
    import shutil

    from ..config import get_settings

    dest = get_settings().data_dir / "runs" / job_id
    out: list[Optional[str]] = []
    for key in ("path", "xlsx"):
        src = result.get(key)
        if not src or not Path(src).is_file():
            out.append(src)
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / Path(src).name
            shutil.copy2(src, target)
            out.append(str(target))
        except OSError:
            out.append(src)              # keep the shared path rather than lose the link
    for extra in ("md",):                # the report travels with the run too
        src = result.get(extra)
        if src and Path(src).is_file():
            try:
                shutil.copy2(src, dest / Path(src).name)
            except OSError:
                pass
    return out[0], out[1]


def _brand(job_id: str, result: dict) -> None:
    """Stamp the archived Excel and report with the system name and logo; never fails the run."""
    from ..config import get_settings
    from . import branding

    dest = get_settings().data_dir / "runs" / job_id
    files = [str(dest / Path(result[k]).name) for k in ("xlsx", "md") if result.get(k)]
    files = [f for f in files if Path(f).is_file()]      # archived copies only, never out/
    if not files:
        return
    try:
        with session() as db:
            branding.stamp_run_files(db, files)
    except Exception:  # pragma: no cover - branding must never cost a valuation
        traceback.print_exc()


def persist_run(db: Session, calc: dict, *, status: str = "done", ai_mode: str = "none", trigger: str = "manual",
                source: str = "v2", verify_pass: Optional[bool] = None, calc_path: Optional[str] = None,
                xlsx_path: Optional[str] = None, run_at: Optional[dt.datetime] = None,
                ai_cost: float = 0.0, tokens_in: int = 0, tokens_out: int = 0,
                valuation_date: Optional[str] = None, requested_by: Optional[str] = None,
                plan: Optional[dict] = None) -> Run:
    """``valuation_date`` is the day the run was made for. Built-in specs (NVDA, 1830.HK) carry a
    fixed assumption-review date in ``calc["valuation_date"]``, which must not stand in for it."""
    base = calc.get("fair_value_per_share")
    run = Run(
        ticker=calc["ticker"], company=calc.get("asset"),
        valuation_date=str(valuation_date or calc.get("valuation_date"))[:10],
        status=status, trigger=trigger, ai_mode=ai_mode, source=source,
        price=calc.get("price"), price_date=calc.get("price_date"),
        trading_ccy=trading_ccy(calc), model_ccy=calc.get("currency"),
        composite_value=base, weights={"dcf": 1.0},   # relative / DDM join in phase 3
        wacc=((calc.get("discount") or {}).get("wacc") or {}).get("value"),
        terminal_g=(calc.get("terminal") or {}).get("g"),
        verify_pass=verify_pass, assumption_source=calc.get("assumption_source"),
        ai_cost_usd=ai_cost, ai_tokens_in=tokens_in, ai_tokens_out=tokens_out,
        calc_json_path=calc_path, xlsx_path=xlsx_path, data_gaps=calc.get("data_gap_codes") or calc.get("data_gaps") or [],
        requested_by=requested_by,
        statement_source=(calc.get("statement_source") or {}).get("source") or ("builtin" if calc.get("ticker") in BUILTIN else "yahoo"),
        ruleset_version=(calc.get("statement_source") or {}).get("ruleset_version"),
        adjustment_plan=plan, plan_key=plan_key(plan),
    )
    if run_at is not None:
        # store UTC so every row reads back the same way, whatever offset the source used
        run.run_at = run_at.astimezone(dt.timezone.utc) if run_at.tzinfo else run_at
    for s in calc.get("scenarios") or []:
        key = SCENARIOS.get(s.get("name"))
        if key and s.get("fair_value") is not None:
            run.methods.append(MethodResult(method="dcf", scenario=key, value=float(s["fair_value"]),
                                            weight=1.0 if key == "base" else None))
    if not any(m.scenario == "base" for m in run.methods) and base is not None:
        run.methods.append(MethodResult(method="dcf", scenario="base", value=float(base), weight=1.0))
    db.add(run)
    db.flush()
    return run


def _usage_by_task(usage: dict) -> dict[str, dict]:
    """Provider-reported usage grouped by AI task (via the LLMClient tag)."""
    tag_to_task = {t.llm_tag: t.key for t in pricing.TASKS if t.llm_tag}
    out: dict[str, dict] = {}
    for ev in (usage or {}).get("events") or []:
        key = tag_to_task.get(ev.get("tag"))
        if not key:
            continue
        agg = out.setdefault(key, {"tokens_in": 0, "tokens_out": 0, "requests": 0, "reported": True})
        agg["requests"] += 1
        if ev.get("reported"):
            agg["tokens_in"] += int(ev.get("prompt_tokens") or 0)
            agg["tokens_out"] += int(ev.get("completion_tokens") or 0)
        else:
            agg["reported"] = False
    return out


BUILTIN = ("NVDA", "1830.HK")          # hand-verified specs: their numbers are fixed in name_specs.py


def plan_key(plan: Optional[dict]) -> str:
    """Part of what makes two valuations the same (D12): ticker, date, AI mode and this."""
    return f"{plan['kind']}:{plan['id']}:v{plan['version']}" if plan else "system_default"


def resolve_plan(db: Session, ticker: str, user_id: Optional[str], choice: str) -> Optional[dict]:
    """The adjustment plan a valuation will use, or None (system default, or the statements are not
    from the SEC standard layer: non-US, not fetched yet, a bank or insurer template, or a built-in spec)."""
    from libs.sv_data.standard import template_for_sic

    from ..models import User
    from . import adjustments, statements

    filer = statements.filer_for(db, ticker)
    if filer is None or not filer.facts or template_for_sic(filer.sic) != "general":
        return None
    user = db.get(User, user_id) if user_id else None
    if user is None:
        choice = "central" if choice in ("auto", "central") else "none"
        user = User(id="", display_name="")
    p = adjustments.chosen(db, ticker, user, choice)
    return {k: p[k] for k in ("kind", "id", "version", "label")} if p else None


def statements_input(db: Session, ticker: str, plan: Optional[dict]) -> Optional[dict]:
    """Annual standardised statements (with exactly that plan version applied) for the engine, or None
    when the valuation keeps reading Yahoo's statements."""
    from libs.sv_data.standard import template_for_sic

    from ..models import AdjustmentPlan
    from . import adjustments, statements

    filer = statements.filer_for(db, ticker)
    if filer is None or not filer.facts or template_for_sic(filer.sic) != "general":
        return None
    applied = None
    if plan:
        row = db.get(AdjustmentPlan, plan["id"])
        ver = adjustments.version_row(db, row, plan["version"]) if row else None
        applied = {**plan, "adjustments": adjustments.to_adjustments(adjustments.rows(db, ver))} if ver else None
    return {"ticker": ticker, **statements.standard_statements(db, filer, years=10, ticker=ticker, period="annual",
                                                               plan=applied)}


def execute(job_id: str, payload: dict) -> None:
    """Worker entry (arq or inline thread). Never raises: the outcome is in the job and the DB."""
    from src.pipeline.orchestrator import ValuationCancelled, run_ticker

    st = store()
    job = st.get(job_id)
    if job is None:
        return
    steps = job["steps"]
    if st.is_cancelled(job_id):
        st.update(job_id, status="cancelled", finished_at=now_iso(), activity="已在開始前取消")
        return
    st.update(job_id, status="running", started_at=now_iso(), activity="開始估值…")

    def find(key: str) -> Optional[dict]:
        return next((s for s in steps if s["key"] == key), None)

    def progress(key: str, activity: Optional[str] = None, skip: Optional[str] = None) -> None:
        if key == "ai_extract":
            return
        target = find(AI_HOOKS.get(key, key))
        for s in steps:                       # whatever was running has finished …
            # … except T3: the v1 pipeline makes the AI call inside the "spec" step
            if s["status"] == "active" and not (key == "spec" and s["key"] == "T3"):
                s["status"] = "done"
        if target is not None:
            if skip and target["status"] != "skipped":
                target.update(status="skipped", note=skip)
            elif target["status"] == "pending":
                target["status"] = "active"
                target["started_at"] = now_iso()
        st.update(job_id, steps=steps, progress=_progress(steps), activity=activity or (target or {}).get("label"))

    with session() as db:
        cfg = settings_store.llm_cfg(db)
        prices = settings_store.price_table(db)
    model = cfg["llm"]["model"]
    local = pricing.is_local(cfg["llm"]["base_url"])
    price = {"model": model, "input": 0.0, "cached_input": 0.0, "output": 0.0} if local else pricing.find_price(model, prices)
    use_llm = bool(payload.get("use_llm"))
    use_t3 = use_llm and any(s["key"] == "T3" and s["status"] == "pending" for s in steps)
    status, error, result = "done", None, None
    plan = job.get("plan")
    lib_ctx: list[dict] = []
    try:
        with session() as db:
            std_input = statements_input(db, job["ticker"], plan)
            from . import doc_library
            ranked_ctx = doc_library.retrieve_chunks(
                db, job["ticker"],
                "revenue guidance outlook growth margin segment geographic region country 業績 指引 展望 增長 毛利率 分部 地區 收入分布",
                top_k=6, max_tokens=2000,
            )
            lib_ctx = [
                {"title": rc.chunk.title, "section": rc.chunk.section, "date": rc.chunk.doc_date,
                 "excerpt": rc.chunk.content[:600]}
                for rc in ranked_ctx if rc.score >= 0.18
            ]
    except Exception:          # the standard layer must never stop a valuation: Yahoo's statements still work
        traceback.print_exc()
        std_input = None

    try:
        import inspect
        rt_params = inspect.signature(run_ticker).parameters
        extra_kw: dict = {}
        if "prefer_generic" in rt_params:
            extra_kw["prefer_generic"] = True
        if "library_context" in rt_params and lib_ctx:
            extra_kw["library_context"] = lib_ctx
        result = run_ticker(
            job["ticker"], cfg=cfg, use_llm=use_t3, valuation_date=job["valuation_date"],
            progress=progress, should_stop=lambda: st.is_cancelled(job_id), skip_extract=True,
            apply_adjustments=False,      # never the shared v1 file (D12): plans are applied to the standard layer
            statements=std_input,
            **extra_kw,
        )
    except ValuationCancelled:
        status, error = "cancelled", "用戶已停止估值（已完成的步驟保留）"
    except Exception as exc:  # report, never crash the worker
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    calc = (result or {}).get("json") or {}
    # Run all remaining selected AI tasks (T1, T2, T4, T5, T6, T7, T8) right after the engine finishes
    if status == "done" and calc:
        from . import valuation_ai

        def _on_post_step(k: str, act: str) -> None:
            so_far_cost = sum(float(s.get("cost_usd") or 0.0) for s in steps if s.get("kind") == "ai")
            so_far_in = sum(int(s.get("tokens_in") or 0) for s in steps if s.get("kind") == "ai")
            so_far_out = sum(int(s.get("tokens_out") or 0) for s in steps if s.get("kind") == "ai")
            st.update(
                job_id,
                steps=steps,
                progress=_progress(steps),
                activity=act,
                cost_usd=round(so_far_cost, 6),
                tokens_in=so_far_in,
                tokens_out=so_far_out,
            )

        try:
            with session() as db:
                ai_analysis, _post_events = valuation_ai.run_post_tasks(
                    db, job["ticker"], calc, steps, cfg=cfg, price=price, use_llm=use_llm,
                    on_step_change=_on_post_step, should_stop=lambda: st.is_cancelled(job_id),
                )
            if ai_analysis:
                calc["ai_analysis"] = ai_analysis
        except Exception:
            traceback.print_exc()

        st.update(job_id, steps=steps, progress=_progress(steps), activity="正在歸檔估值結果與寫入資料庫…")

    usage = _usage_by_task(calc.get("llm_usage") or {})
    gaps = " ".join(calc.get("data_gaps") or [])
    gap_codes = {g.get("code") for g in calc.get("data_gap_codes") or [] if isinstance(g, dict)}
    total_cost, total_in, total_out = 0.0, 0, 0
    for s in steps:
        if s["status"] == "active":
            s["status"] = "done" if status == "done" else ("cancelled" if status == "cancelled" else "failed")
        if s["kind"] != "ai" or s["status"] == "skipped":
            continue
        u = usage.get(s["key"])
        if u:
            c = pricing.cost(u["tokens_in"], u["tokens_out"], price) or 0.0
            s.update(tokens_in=u["tokens_in"], tokens_out=u["tokens_out"], cost_usd=round(c, 6),
                     usage_reported=u["reported"])
            total_cost, total_in, total_out = total_cost + c, total_in + u["tokens_in"], total_out + u["tokens_out"]
            if s["key"] == "T3" and ("ai_failed" in gap_codes or "AI 提案失敗" in gaps):
                s.update(status="failed", note="AI 請求失敗，已改用規則後備", note_code="ai_failed")
            elif s["key"] == "T3" and ("ai_rejected" in gap_codes or "AI 提案未採用" in gaps):
                s.update(note="AI 提案超出資料推算範圍，已退回規則值（費用已產生）", note_code="ai_rejected")
            elif not u["reported"]:
                s.update(note="服務商未回報用量，費用按 0 計", note_code="usage_unreported")
        elif s["key"] == "T3" and s["status"] == "done" and status == "done":
            s.update(status="failed", note="AI 未回覆可用結果，已改用規則後備", note_code="ai_failed")
        elif s["status"] == "done":
            # Post-valuation AI tasks (T1/T2/T4/T5/T6/T7/T8) already recorded their own tokens & cost
            total_cost += float(s.get("cost_usd") or 0.0)
            total_in += int(s.get("tokens_in") or 0)
            total_out += int(s.get("tokens_out") or 0)
        elif s["status"] == "pending":
            s["status"] = "cancelled" if status == "cancelled" else "skipped"
            s["note"] = s.get("note") or ("已停止" if status == "cancelled" else "未執行")
            s["note_code"] = s.get("note_code") or ("cancelled" if status == "cancelled" else "not_run")
    for s in steps:
        if s["kind"] != "ai" and s["status"] == "pending" and status == "done":
            s["status"] = "done"

    calc_path, xlsx_path = (result or {}).get("path"), (result or {}).get("xlsx")
    if status == "done" and calc:
        calc_path, xlsx_path = archive_outputs(job_id, result)
        if calc.get("ai_analysis") and calc_path and Path(calc_path).is_file():
            try:
                import json as _json
                Path(calc_path).write_text(_json.dumps(calc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            except OSError:
                pass
        elif calc.get("ai_analysis") and not calc_path:
            # Fake/headless runs without an exported JSON still persist ai_analysis into the run archive dir
            try:
                import json as _json
                from ..config import get_settings
                dest = get_settings().data_dir / "runs" / job_id
                dest.mkdir(parents=True, exist_ok=True)
                p_json = dest / f"{job['ticker']}_calc.json"
                p_json.write_text(_json.dumps(calc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                calc_path = str(p_json)
            except OSError:
                pass
        _brand(job_id, result)

    with session() as db:
        run = None
        if status == "done" and calc:
            from . import market, valuation_methods
            market.track(db, job["ticker"])
            run = persist_run(db, calc, status="done", ai_mode=job["mode"], trigger=payload.get("trigger") or "manual",
                              verify_pass=result.get("verify_pass"), calc_path=calc_path,
                              xlsx_path=xlsx_path, ai_cost=total_cost, tokens_in=total_in, tokens_out=total_out,
                              valuation_date=job["valuation_date"], requested_by=payload.get("requested_by"),
                              plan=plan if std_input is not None else None)
            # Upgrade Run.composite_value & weights to the industry-aware multi-method composite (D5)
            try:
                mm = valuation_methods.compute_stock_methods(db, job["ticker"])
                comp_val = mm.get("composite_value")
                weights = mm.get("weights") or {}
                if comp_val is not None and comp_val > 0 and weights:
                    run.composite_value = float(comp_val)
                    run.weights = dict(weights)
                    existing_methods = {(m.method, m.scenario) for m in run.methods}
                    for m in mm.get("methods") or []:
                        m_id = m.get("method")
                        if m_id and m_id != "dcf" and m.get("base") is not None and (m_id, "base") not in existing_methods:
                            db.add(MethodResult(run_id=run.id, method=m_id, scenario="base",
                                                value=float(m["base"]), weight=weights.get(m_id)))
                    db.flush()
            except Exception:
                traceback.print_exc()
        else:
            run = Run(ticker=job["ticker"], valuation_date=job["valuation_date"], status=status,
                      requested_by=payload.get("requested_by"),
                      ai_mode=job["mode"], trigger=payload.get("trigger") or "manual", error=error,
                      ai_cost_usd=total_cost, ai_tokens_in=total_in, ai_tokens_out=total_out)
            db.add(run)
            db.flush()
        for s in steps:
            if s["kind"] == "ai":
                db.add(AiTask(job_id=job_id, run_id=run.id, ticker=job["ticker"], task=s["key"],
                              status=s["status"], model=model or None,
                              est_tokens_in=s.get("est_tokens_in") or 0, est_tokens_out=s.get("est_tokens_out") or 0,
                              est_cost_usd=s.get("est_cost_usd") or 0.0, tokens_in=s.get("tokens_in") or 0,
                              tokens_out=s.get("tokens_out") or 0, cost_usd=s.get("cost_usd") or 0.0,
                              usage_reported=bool(s.get("usage_reported")), note=s.get("note")))
        run_id = run.id
        final_fv = run.composite_value if run is not None else None

    summary = None
    if status == "done":
        fv_out = final_fv if final_fv is not None else calc.get("fair_value_per_share")
        summary = {"fair_value": fv_out, "price": calc.get("price"),
                   "currency": trading_ccy(calc), "verify_pass": result.get("verify_pass"),
                   "warning": calc.get("result_warning")}
    st.update(job_id, status=status, finished_at=now_iso(), steps=steps,
              progress=100 if status == "done" else _progress(steps),
              activity={"done": "估值完成", "cancelled": "已停止", "failed": "估值失敗"}[status],
              cost_usd=round(total_cost, 6), tokens_in=total_in, tokens_out=total_out,
              run_id=run_id, result=summary, error=error)
