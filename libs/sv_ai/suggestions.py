"""T1 (checks-layer input), T2 (news/catalysts) and strict 100%-rejection citation validator (beta.2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .news import NewsItem


@dataclass(frozen=True)
class AllowedSource:
    id: str
    title: str
    url: str
    date: Optional[str] = None


@dataclass(frozen=True)
class SuggestionCandidate:
    task_key: str               # T1 | T2 | T7
    title: str
    detail: str
    source_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerifiedSuggestion:
    task_key: str
    title: str
    detail: str
    source_id: str
    source_title: str
    source_url: str
    source_date: Optional[str]
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_key": self.task_key,
            "title": self.title,
            "detail": self.detail,
            "source_id": self.source_id,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "source_date": self.source_date,
            "payload": self.payload,
        }


def validate_suggestions(
    candidates: list[SuggestionCandidate],
    allowed: dict[str, AllowedSource],
) -> tuple[list[VerifiedSuggestion], int]:
    """Strict source citation gate (roadmap §7 beta.2):
    - Every suggestion MUST reference a ``source_id`` present in ``allowed`` with a clickable URL.
    - Any candidate referencing an unknown/fabricated source_id or empty URL is 100% REJECTED.
    Returns ``(verified_suggestions, rejected_count)``.
    """
    verified: list[VerifiedSuggestion] = []
    rejected = 0
    for c in candidates:
        src = allowed.get(c.source_id)
        if src is None or not src.url or not src.url.startswith(("http://", "https://", "/")):
            rejected += 1
            continue
        if not c.title.strip() or not c.detail.strip():
            rejected += 1
            continue
        verified.append(VerifiedSuggestion(
            task_key=c.task_key,
            title=c.title.strip()[:200],
            detail=c.detail.strip()[:2000],
            source_id=src.id,
            source_title=src.title[:240],
            source_url=src.url[:500],
            source_date=src.date,
            payload=dict(c.payload or {}),
        ))
    return verified, rejected


def generate_t1_suggestions(
    ticker: str,
    std: dict[str, Any],
    *,
    llm_candidates: Optional[Callable[[list[dict], dict[str, AllowedSource]], list[SuggestionCandidate]]] = None,
) -> tuple[list[VerifiedSuggestion], int]:
    """T1: Anomaly detection & adjustment proposals driven strictly by the checks layer (roadmap §3.6)."""
    years = [y["label"] for y in (std.get("years") or [])]
    # Build allowed source map from checks failures and restated cells
    allowed: dict[str, AllowedSource] = {}
    period_url: dict[str, tuple[str, Optional[str]]] = {}
    for row in std.get("rows") or []:
        for y, cell in zip(years, row.get("cells") or []):
            if cell and (cell.get("document_url") or cell.get("index_url")):
                period_url.setdefault(y, (cell.get("document_url") or cell.get("index_url"), cell.get("filed")))

    failures = (std.get("checks") or {}).get("failures") or []
    candidates: list[SuggestionCandidate] = []

    for idx, f in enumerate(failures, 1):
        period = str(f.get("period") or "")
        rule_id = str(f.get("rule_id") or "")
        sev = str(f.get("severity") or "warning")
        url, filed = period_url.get(period, (f"/stock/{ticker}/statements", None))
        sid = f"check:{rule_id}:{period}:{idx}"
        allowed[sid] = AllowedSource(
            id=sid,
            title=f"{ticker} {period} check:{rule_id} ({sev})",
            url=url,
            date=filed,
        )
        vals = f.get("values") or {}
        diff = f.get("difference")
        detail = (
            f"Check rule '{rule_id}' ({sev}) flagged {period}"
            + (f" with relative difference {diff}" if diff else "")
            + (f" — values: {vals}" if vals else "")
            + ". Review filing source or record an adjustment plan entry if warranted."
        )
        candidates.append(SuggestionCandidate(
            task_key="T1",
            title=f"[{sev.upper()}] {period} {rule_id}",
            detail=detail,
            source_id=sid,
            payload={"rule_id": rule_id, "period": period, "severity": sev, "values": vals, "difference": diff},
        ))

    # Also surface material restatements from the standard layer
    for row in std.get("rows") or []:
        code = row["code"]
        for y, cell in zip(years[:5], (row.get("cells") or [])[:5]):
            if not cell or cell.get("change") != "restated":
                continue
            url = cell.get("document_url") or cell.get("index_url") or f"/stock/{ticker}/statements"
            sid = f"restated:{code}:{y}"
            if sid in allowed:
                continue
            allowed[sid] = AllowedSource(
                id=sid,
                title=f"{ticker} {y} {code} ({cell.get('form') or '10-K'})",
                url=url,
                date=cell.get("filed"),
            )
            candidates.append(SuggestionCandidate(
                task_key="T1",
                title=f"Restatement: {y} {code}",
                detail=f"{code} in {y} was restated from {cell.get('original_value')} to {cell.get('value')} "
                       f"(filed {cell.get('filed') or 'N/A'}). Verify impact on trend assumptions.",
                source_id=sid,
                payload={"item": code, "period": y, "original_value": cell.get("original_value"), "value": cell.get("value")},
            ))
            if len(candidates) >= 15:
                break
        if len(candidates) >= 15:
            break

    if llm_candidates is not None and allowed:
        candidates = llm_candidates(failures, allowed)

    return validate_suggestions(candidates, allowed)


def generate_t2_suggestions(
    ticker: str,
    news_items: list[NewsItem],
    *,
    llm_candidates: Optional[Callable[[list[NewsItem], dict[str, AllowedSource]], list[SuggestionCandidate]]] = None,
) -> tuple[list[VerifiedSuggestion], int]:
    """T2: News, filing & catalyst extraction strictly grounded in ``NewsItem`` sources."""
    allowed: dict[str, AllowedSource] = {
        n.id: AllowedSource(id=n.id, title=n.title, url=n.url, date=n.published_at)
        for n in news_items
        if n.url
    }
    if llm_candidates is not None and allowed:
        candidates = llm_candidates(news_items, allowed)
    else:
        candidates = [
            SuggestionCandidate(
                task_key="T2",
                title=n.title,
                detail=n.summary or f"{n.source.upper()} {n.kind}: {n.title} ({n.published_at or ''}).",
                source_id=n.id,
                payload={"source": n.source, "kind": n.kind, "published_at": n.published_at},
            )
            for n in news_items[:8]
            if n.id in allowed
        ]
    return validate_suggestions(candidates, allowed)


def generate_t7_suggestions(
    ticker: str,
    methods_out: dict[str, Any],
    *,
    latest_run: Optional[dict[str, Any]] = None,
    llm_candidates: Optional[Callable[[dict[str, Any], dict[str, AllowedSource]], list[SuggestionCandidate]]] = None,
) -> tuple[list[VerifiedSuggestion], int]:
    """T7: Explain valuation synthesis, method divergence, D5 financial warnings, and Reverse DCF market expectations."""
    allowed: dict[str, AllowedSource] = {}
    candidates: list[SuggestionCandidate] = []
    val_url = f"/stock/{ticker}/valuation"
    run_id = (latest_run or {}).get("id")
    run_date = (latest_run or {}).get("valuation_date")
    report_url = f"/api/v1/runs/{run_id}/report" if run_id else val_url

    composite = methods_out.get("composite_value")
    price = methods_out.get("price")
    ccy = methods_out.get("currency") or ""
    weights = methods_out.get("weights") or {}
    warnings = methods_out.get("warnings") or []
    rdcf = methods_out.get("reverse_dcf")

    if composite is not None:
        sid = f"val:composite:{run_id or 'latest'}"
        allowed[sid] = AllowedSource(
            id=sid,
            title=f"{ticker} multi-method valuation ({run_date or 'latest'})",
            url=report_url,
            date=run_date,
        )
        upside = ((float(composite) / float(price)) - 1.0) * 100.0 if (price and float(price) > 0) else None
        w_text = ", ".join(f"{k.upper()} {round(float(w) * 100)}%" for k, w in weights.items())
        detail = (
            f"Composite fair value is {ccy} {float(composite):.2f}"
            + (f" vs market price {ccy} {float(price):.2f} ({upside:+.1f}%)" if upside is not None else "")
            + (f" using weights [{w_text}]." if w_text else ".")
        )
        if "financial_fcff_warning" in warnings:
            detail += " D5 financial-sector rule active: RI/DDM/P/B-ROE are primary; FCFF is informational only."
        if "methods_divergence_warning" in warnings:
            detail += " Primary methods diverge by >35% — inspect individual Football Field ranges and assumptions."
        candidates.append(SuggestionCandidate(
            task_key="T7",
            title=f"Valuation synthesis: {ccy} {float(composite):.2f}" + (f" ({upside:+.1f}%)" if upside is not None else ""),
            detail=detail,
            source_id=sid,
            payload={"composite_value": composite, "price": price, "weights": weights, "warnings": warnings},
        ))

    if rdcf and rdcf.get("implied_growth") is not None:
        sid = f"val:rdcf:{run_id or 'latest'}"
        allowed[sid] = AllowedSource(
            id=sid,
            title=f"{ticker} Reverse DCF implied growth ({run_date or 'latest'})",
            url=val_url,
            date=run_date,
        )
        imp = float(rdcf["implied_growth"]) * 100.0
        base_g = float(rdcf["base_growth"]) * 100.0 if rdcf.get("base_growth") is not None else None
        assessment = str(rdcf.get("assessment") or "aligned")
        detail = (
            f"Current price implies a 5-year FCF CAGR of {imp:.1f}% (WACC {float(rdcf.get('wacc', 0)) * 100:.2f}%, "
            f"terminal g {float(rdcf.get('terminal_g', 0)) * 100:.2f}%)"
            + (f" vs model base growth {base_g:.1f}% ({assessment})." if base_g is not None else f" ({assessment}).")
        )
        candidates.append(SuggestionCandidate(
            task_key="T7",
            title=f"Reverse DCF implies {imp:.1f}% 5Y FCF CAGR ({assessment})",
            detail=detail,
            source_id=sid,
            payload={"implied_growth": rdcf["implied_growth"], "base_growth": rdcf.get("base_growth"), "assessment": assessment},
        ))

    if llm_candidates is not None and allowed:
        candidates = llm_candidates(methods_out, allowed)

    return validate_suggestions(candidates, allowed)
