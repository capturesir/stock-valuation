"""Execute valuation AI tasks T1, T2, T4, T5, T6, T7, T8 inside a valuation job with real token/cost accounting."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.sv_ai import (
    default_peer_tickers,
    run_t4_scenarios,
    run_t5_peers,
    run_t6_red_team,
    run_t7_report,
    run_t8_segments,
)

from .. import pricing
from ..jobs import now_iso
from ..models import DocumentChunk, Run, StockDocument
from . import ai_tasks, statements, valuation_methods

log = logging.getLogger("sv.valuation_ai")
POST_TASK_ORDER = ("T1", "T2", "T4", "T5", "T6", "T7", "T8")


def _make_llm_caller(cfg: dict, use_llm: bool):
    """Build an LLMClient + callable ``(system, user, tag) -> Optional[dict]`` when LLM is configured."""
    if not use_llm:
        return None, None
    try:
        from src.llm.client import LLMClient
        llm = dict((cfg or {}).get("llm") or {})
        llm["timeout_seconds"] = min(float(llm.get("timeout_seconds") or 20), 20.0)
        llm["max_retries"] = 1
        client = LLMClient({"llm": llm})
        if not client.available():
            return None, None
    except Exception:
        return None, None

    def _call(system_prompt: str, user_text: str, tag: str) -> Optional[dict[str, Any]]:
        try:
            res = client.chat_json(system_prompt, user_text, tag=tag)
            if res.ok and isinstance(res.data, dict):
                return res.data
        except Exception as exc:
            log.debug("LLM task %s fallback to rules: %s", tag, type(exc).__name__)
        return None

    return client, _call


def _segment_snippets(db: Session, ticker: str, limit: int = 6) -> list[dict[str, str]]:
    """Fetch segment-related chunks from the stock's Document Library (if synced)."""
    docs = db.scalars(
        select(StockDocument).where(StockDocument.ticker == ticker).order_by(StockDocument.created_at.desc()).limit(10)
    ).all()
    if not docs:
        return []
    doc_map = {d.id: d.title for d in docs}
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id.in_(list(doc_map)))
        .order_by(DocumentChunk.id)
        .limit(80)
    ).all()
    kws = ("segment", "revenue by", "business unit", "operating segment", "disaggregat", "分部", "業務", "业务", "板塊", "板块")
    picked = [
        {"title": doc_map.get(c.document_id, "Document"), "section": c.section or "", "text": (c.content or "")[:600]}
        for c in chunks
        if any(kw in (c.content or "").lower() or kw in (c.section or "").lower() for kw in kws)
    ]
    return picked[:limit]


def run_post_tasks(
    db: Session,
    ticker: str,
    calc: dict[str, Any],
    steps: list[dict[str, Any]],
    *,
    cfg: dict,
    price: Optional[dict],
    use_llm: bool,
    on_step_change: Optional[Callable[[str, str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run all selected & pending post-engine AI tasks (T1, T2, T4, T5, T6, T7, T8).

    Emits granular live sub-step progress via ``on_step_change`` and updates each step's
    ``note`` and ``started_at`` so the UI always shows exact real-time work in progress.
    """
    step_by_key = {s["key"]: s for s in steps}
    todo = [k for k in POST_TASK_ORDER if (k in step_by_key and step_by_key[k]["status"] == "pending")]
    if not todo:
        return {}, []

    client, llm_chat = _make_llm_caller(cfg, use_llm)
    model_name = str(((cfg or {}).get("llm") or {}).get("model") or "AI")
    analysis: dict[str, Any] = {}

    def _emit(key: str, detail: str) -> None:
        step = step_by_key.get(key)
        if step is not None:
            step["note"] = detail
            step["note_code"] = None
        if on_step_change:
            on_step_change(key, f"{key} · {detail}")

    # Shared context fetched once for all tasks
    if on_step_change:
        on_step_change(todo[0], "正在載入標準化財報與多方法估值上下文…")
    filer = statements.filer_for(db, ticker)
    std: Optional[dict] = None
    if filer is not None and filer.synced_at is not None:
        try:
            std = statements.standard_statements(db, filer, view="latest", years=6, ticker=ticker, period="annual")
        except Exception:
            std = None

    methods_out: Optional[dict] = None
    try:
        methods_out = valuation_methods.compute_stock_methods(db, ticker)
    except Exception:
        methods_out = None

    prev_row = db.scalar(
        select(Run).where(Run.ticker == ticker, Run.status == "done").order_by(Run.run_at.desc()).limit(1)
    )
    prev_run = (
        {"valuation_date": prev_row.valuation_date, "composite_value": prev_row.composite_value,
         "wacc": prev_row.wacc, "terminal_g": prev_row.terminal_g, "price": prev_row.price}
        if prev_row is not None else None
    )

    industry = calc.get("generic_industry") or (filer.sic_description if filer else None)

    for key in todo:
        if should_stop and should_stop():
            step_by_key[key].update(status="cancelled", note="用戶已停止", note_code="cancelled")
            continue

        step = step_by_key[key]
        step["status"] = "active"
        step["started_at"] = now_iso()
        ev_before = len((client.usage.get("events") if client else None) or [])

        try:
            if key == "T1":
                _emit("T1", "正在掃描標準化財報異常與檢查規則…")
                res1 = ai_tasks.generate_and_store_suggestions(db, ticker, tasks=["T1"], include_remote_news=False)
                items1 = [{"title": r.title, "detail": r.detail, "source_url": r.source_url, "status": r.status}
                          for r in res1["rows"] if r.task_key == "T1"][:10]
                analysis["T1"] = {
                    "task": "T1",
                    "created": res1["created"],
                    "rejected_unverified": res1["rejected_unverified"],
                    "items": items1,
                }
                done_note = f"掃描完成（{len(items1)} 項異常／提醒）"

            elif key == "T2":
                _emit("T2", "正在抓取近期新聞並篩選估值相關催化事件…")
                res2 = ai_tasks.generate_and_store_suggestions(db, ticker, tasks=["T2"], include_remote_news=True)
                items2 = [{"title": r.title, "detail": r.detail, "source_url": r.source_url, "source_date": r.source_date}
                          for r in res2["rows"] if r.task_key == "T2"][:10]
                analysis["T2"] = {
                    "task": "T2",
                    "created": res2["created"],
                    "rejected_unverified": res2["rejected_unverified"],
                    "items": items2,
                }
                done_note = f"整理完成（{len(items2)} 則新聞催化）"

            elif key == "T4":
                _emit("T4", f"正在呼叫 {model_name} 建構悲觀／基準／樂觀情境敘事…" if llm_chat else "正在計算三情境機率加權理論價…")
                t4 = run_t4_scenarios(ticker, calc, llm_chat=llm_chat)
                analysis["T4"] = t4
                src = "AI" if t4.get("source") == "llm" else "規則"
                pw = t4.get("prob_weighted_value")
                done_note = f"{src}完成" + (f" · 機率加權 {pw:,.2f}" if isinstance(pw, (int, float)) else "")

            elif key == "T5":
                _emit("T5", "正在比對同業標準化財報與中位數…")
                peer_tickers = [ticker] + default_peer_tickers(ticker, industry)
                peer_cmp = statements.peer_financial_compare(db, peer_tickers) if len(peer_tickers) >= 2 else None
                if llm_chat:
                    _emit("T5", f"已載入 {len(peer_tickers) - 1} 家候選同業，正在呼叫 {model_name} 評析可比定位…")
                t5 = run_t5_peers(ticker, industry, peer_cmp, llm_chat=llm_chat)
                analysis["T5"] = t5
                src = "AI" if t5.get("source") == "llm" else "規則"
                done_note = f"{src}完成 · {len(t5.get('peers') or [])} 家同業"

            elif key == "T6":
                _emit("T6", f"正在執行紅隊反方審查並呼叫 {model_name} 檢視矛盾與高估風險…" if llm_chat else "正在執行規則紅隊反方檢核…")
                t6 = run_t6_red_team(ticker, calc, methods_out, std=std, llm_chat=llm_chat)
                analysis["T6"] = t6
                src = "AI" if t6.get("source") == "llm" else "規則"
                done_note = f"{src}完成 · {len(t6.get('findings') or [])} 項風險檢核"

            elif key == "T7":
                _emit("T7", f"正在呼叫 {model_name} 撰寫估值報告與變動歸因…" if llm_chat else "正在整理估值報告與變動歸因…")
                ai_tasks.generate_and_store_suggestions(db, ticker, tasks=["T7"], include_remote_news=False)
                t7 = run_t7_report(ticker, calc, methods_out, prev_run=prev_run, llm_chat=llm_chat)
                analysis["T7"] = t7
                src = "AI" if t7.get("source") == "llm" else "規則"
                done_note = f"{src}完成"

            elif key == "T8":
                _emit("T8", "正在從文件庫檢索分部附註與業務驅動段落…")
                snippets = _segment_snippets(db, ticker)
                if llm_chat:
                    if snippets:
                        _emit("T8", f"已找到 {len(snippets)} 段文件庫分部內容，正在呼叫 {model_name} 拆解業務分部與驅動因子（最多約 20 秒）…")
                    else:
                        _emit("T8", f"文件庫尚無分部附註，正依財務軌跡呼叫 {model_name} 拆解核心驅動因子（最多約 20 秒）…")
                else:
                    _emit("T8", f"正以規則整理分部與營收軌跡（文件庫命中 {len(snippets)} 段）…")
                t8 = run_t8_segments(ticker, calc, doc_snippets=snippets, llm_chat=llm_chat)
                analysis["T8"] = t8
                src = "AI" if t8.get("source") == "llm" else "規則"
                done_note = f"{src}完成 · 拆解出 {len(t8.get('segments') or [])} 個分部"
            else:
                done_note = "完成"

            step["status"] = "done"
            step["note"] = done_note
            step["note_code"] = None
            # Attach real LLM usage if this task invoked the LLM
            new_events = ((client.usage.get("events") if client else None) or [])[ev_before:]
            if new_events:
                tin = sum(int(e.get("prompt_tokens") or 0) for e in new_events)
                tout = sum(int(e.get("completion_tokens") or 0) for e in new_events)
                rep = all(bool(e.get("reported", True)) for e in new_events)
                c = pricing.cost(tin, tout, price) or 0.0
                step.update(tokens_in=tin, tokens_out=tout, cost_usd=round(c, 6), usage_reported=rep)
            else:
                step.update(tokens_in=0, tokens_out=0, cost_usd=0.0, usage_reported=True)
            if on_step_change:
                on_step_change(key, f"{key} {done_note}")
        except Exception as exc:
            log.warning("Post-valuation AI task %s failed for %s: %s", key, ticker, type(exc).__name__)
            step.update(status="failed", note=f"{type(exc).__name__}: {exc}"[:200], note_code=None)
            if on_step_change:
                on_step_change(key, f"{key} 失敗（已改用後備）")

    events = list((client.usage.get("events") if client else None) or [])
    return analysis, events
