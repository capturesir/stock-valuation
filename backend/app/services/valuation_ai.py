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
    docs = db.scalars(select(StockDocument).where(StockDocument.ticker == ticker).order_by(StockDocument.created_at.desc()).limit(10)).all()
    if not docs:
        return []
    doc_map = {d.id: d.title for d in docs}
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id.in_(list(doc_map)))
        .order_by(DocumentChunk.id)
        .limit(80)
    ).all()
    picked = [
        {"title": doc_map.get(c.document_id, "Document"), "section": c.section or "", "text": (c.text or "")[:600]}
        for c in chunks
        if any(kw in (c.text or "").lower() or kw in (c.section or "").lower()
               for kw in ("segment", "revenue by", "business unit", "operating segment", "分部", "業務"))
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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run all selected & pending post-engine AI tasks (T1, T2, T4, T5, T6, T7, T8).

    Returns ``(ai_analysis_dict, llm_usage_events)``. Never raises: any individual task failure
    falls back cleanly to deterministic rule-based output so a valuation always completes.
    """
    step_by_key = {s["key"]: s for s in steps}
    todo = [k for k in POST_TASK_ORDER if (k in step_by_key and step_by_key[k]["status"] == "pending")]
    if not todo:
        return {}, []

    client, llm_chat = _make_llm_caller(cfg, use_llm)
    analysis: dict[str, Any] = {}

    # Shared context fetched once for all tasks
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
        step = step_by_key[key]
        step["status"] = "active"
        if on_step_change:
            on_step_change(key, step.get("label") or key)

        ev_before = len((client.usage.get("events") if client else None) or [])
        try:
            if key == "T1":
                res1 = ai_tasks.generate_and_store_suggestions(db, ticker, tasks=["T1"], include_remote_news=False)
                analysis["T1"] = {
                    "task": "T1",
                    "created": res1["created"],
                    "rejected_unverified": res1["rejected_unverified"],
                    "items": [{"title": r.title, "detail": r.detail, "source_url": r.source_url, "status": r.status}
                              for r in res1["rows"] if r.task_key == "T1"][:10],
                }
            elif key == "T2":
                res2 = ai_tasks.generate_and_store_suggestions(db, ticker, tasks=["T2"], include_remote_news=True)
                analysis["T2"] = {
                    "task": "T2",
                    "created": res2["created"],
                    "rejected_unverified": res2["rejected_unverified"],
                    "items": [{"title": r.title, "detail": r.detail, "source_url": r.source_url, "source_date": r.source_date}
                              for r in res2["rows"] if r.task_key == "T2"][:10],
                }
            elif key == "T4":
                analysis["T4"] = run_t4_scenarios(ticker, calc, llm_chat=llm_chat)
            elif key == "T5":
                peer_tickers = [ticker] + default_peer_tickers(ticker, industry)
                peer_cmp = statements.peer_financial_compare(db, peer_tickers) if len(peer_tickers) >= 2 else None
                analysis["T5"] = run_t5_peers(ticker, industry, peer_cmp, llm_chat=llm_chat)
            elif key == "T6":
                analysis["T6"] = run_t6_red_team(ticker, calc, methods_out, std=std, llm_chat=llm_chat)
            elif key == "T7":
                ai_tasks.generate_and_store_suggestions(db, ticker, tasks=["T7"], include_remote_news=False)
                analysis["T7"] = run_t7_report(ticker, calc, methods_out, prev_run=prev_run, llm_chat=llm_chat)
            elif key == "T8":
                snippets = _segment_snippets(db, ticker)
                analysis["T8"] = run_t8_segments(ticker, calc, doc_snippets=snippets, llm_chat=llm_chat)

            step["status"] = "done"
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
        except Exception as exc:
            log.warning("Post-valuation AI task %s failed for %s: %s", key, ticker, type(exc).__name__)
            step.update(status="failed", note=f"{type(exc).__name__}: {exc}"[:200])

    events = list((client.usage.get("events") if client else None) or [])
    return analysis, events
