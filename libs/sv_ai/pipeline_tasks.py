"""Pure-Python builders for valuation AI tasks T4 (scenarios), T5 (peers), T6 (red team), T7 (report & attribution), T8 (segments).

Every function works both WITH an LLM callback ``llm_chat(system, user, tag) -> Optional[dict]``
and WITHOUT an LLM (deterministic rule-based synthesis grounded strictly in model numbers & filings).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

LlmChat = Callable[[str, str, str], Optional[dict[str, Any]]]


# ------------------------------------------------------------------ T4: Scenarios & Probabilities

def run_t4_scenarios(
    ticker: str,
    calc: dict[str, Any],
    *,
    llm_chat: Optional[LlmChat] = None,
) -> dict[str, Any]:
    """Build Bear / Base / Bull scenario narratives, triggers, and validated probability-weighted fair value."""
    scen_map = {s.get("name"): s for s in (calc.get("scenarios") or []) if s.get("fair_value") is not None}
    bear_fv = float((scen_map.get("悲觀") or {}).get("fair_value") or 0.0)
    base_fv = float((scen_map.get("基準") or {}).get("fair_value") or calc.get("fair_value_per_share") or 0.0)
    bull_fv = float((scen_map.get("樂觀") or {}).get("fair_value") or 0.0)
    price = calc.get("price")

    # Default calibrated probabilities (Bear 25%, Base 50%, Bull 25%)
    probs = {"bear": 0.25, "base": 0.50, "bull": 0.25}
    scenarios_out = [
        {
            "key": "bear",
            "fair_value": round(bear_fv, 4),
            "probability": 0.25,
            "trigger": (scen_map.get("悲觀") or {}).get("changes") or "Revenue growth −3pp and EBIT margin −2pp over years 1–4.",
            "narrative": f"{ticker} faces slower demand or margin compression relative to consensus, reducing near-term FCFF and terminal growth.",
        },
        {
            "key": "base",
            "fair_value": round(base_fv, 4),
            "probability": 0.50,
            "trigger": (scen_map.get("基準") or {}).get("changes") or "Consensus/rule path fading to terminal growth under Method A WACC.",
            "narrative": f"{ticker} tracks consensus near-term revenue and reverts gradually toward sustainable long-run growth and reinvestment.",
        },
        {
            "key": "bull",
            "fair_value": round(bull_fv, 4),
            "probability": 0.25,
            "trigger": (scen_map.get("樂觀") or {}).get("changes") or "Revenue growth +3pp and EBIT margin +2pp over years 1–4.",
            "narrative": f"{ticker} sustains stronger share gains and operating leverage longer than the base path assumes.",
        },
    ]

    source = "rules"
    if llm_chat is not None and base_fv > 0:
        sys_p = (
            "You are an institutional equity analyst. Given exact engine DCF Bear/Base/Bull fair values, "
            "return JSON {\"scenarios\": [{\"key\": \"bear\"|\"base\"|\"bull\", \"probability\": float, "
            "\"trigger\": str, \"narrative\": str}]}. Probabilities must be in [0.10, 0.70] and sum to 1.0. "
            "Never alter the numeric fair values."
        )
        payload = {
            "ticker": ticker,
            "price": price,
            "bear_fv": bear_fv,
            "base_fv": base_fv,
            "bull_fv": bull_fv,
            "wacc": ((calc.get("discount") or {}).get("wacc") or {}).get("value"),
            "terminal_g": (calc.get("terminal") or {}).get("g"),
        }
        res = llm_chat(sys_p, json.dumps(payload, ensure_ascii=False), "t4_scenarios")
        if isinstance(res, dict) and isinstance(res.get("scenarios"), list):
            by_k = {str(s.get("key", "")).lower(): s for s in res["scenarios"] if isinstance(s, dict)}
            if {"bear", "base", "bull"} <= set(by_k):
                try:
                    raw_p = {k: max(0.10, min(0.70, float(by_k[k]["probability"]))) for k in ("bear", "base", "bull")}
                    tot = sum(raw_p.values())
                    probs = {k: round(v / tot, 4) for k, v in raw_p.items()}
                    # Keep exact sum = 1.0
                    probs["base"] = round(1.0 - probs["bear"] - probs["bull"], 4)
                    for item in scenarios_out:
                        k = item["key"]
                        item["probability"] = probs[k]
                        if by_k[k].get("trigger"):
                            item["trigger"] = str(by_k[k]["trigger"])[:400]
                        if by_k[k].get("narrative"):
                            item["narrative"] = str(by_k[k]["narrative"])[:600]
                    source = "llm"
                except (TypeError, ValueError, KeyError):
                    pass

    pw_fv = round(bear_fv * probs["bear"] + base_fv * probs["base"] + bull_fv * probs["bull"], 4) if base_fv > 0 else None
    return {
        "task": "T4",
        "source": source,
        "probability_weighted_fv": pw_fv,
        "scenarios": scenarios_out,
    }


# ------------------------------------------------------------------ T5: Peer Screening

INDUSTRY_PEERS: dict[str, list[str]] = {
    "semiconductor": ["NVDA", "AVGO", "AMD", "TSM", "INTC", "QCOM"],
    "software": ["MSFT", "ORCL", "ADBE", "CRM", "NOW"],
    "electronics": ["AAPL", "SONY", "DELL", "HPQ"],
    "information services": ["GOOGL", "META", "0700.HK", "9988.HK"],
    "retail": ["AMZN", "WMT", "COST", "TGT", "HD"],
    "drugs": ["LLY", "JNJ", "PFE", "MRK", "ABBV"],
    "oil/gas": ["XOM", "CVX", "0883.HK", "SHEL", "COP"],
    "bank": ["JPM", "BAC", "WFC", "C", "0005.HK"],
    "insurance": ["BRK-B", "TRV", "MET", "UNH", "1299.HK"],
    "telecom": ["1883.HK", "0941.HK", "VZ", "T", "TMUS"],
    "household products": ["PG", "KO", "PEP", "CL"],
}


def default_peer_tickers(ticker: str, industry: Optional[str]) -> list[str]:
    t = ticker.upper()
    ind = (industry or "").lower()
    for key, peers in INDUSTRY_PEERS.items():
        if key in ind or t in peers:
            return [p for p in peers if p != t][:6]
    return []


def run_t5_peers(
    ticker: str,
    industry: Optional[str],
    peer_compare: Optional[dict[str, Any]] = None,
    *,
    llm_chat: Optional[LlmChat] = None,
) -> dict[str, Any]:
    """Select comparable peers and attach programmatic standardized multiples/ratios."""
    candidates = default_peer_tickers(ticker, industry)
    peers = [
        {"ticker": p, "rationale": f"Industry peer ({industry or 'same sector'}) selected by classification rules."}
        for p in candidates
    ]
    source = "rules"
    if llm_chat is not None:
        sys_p = (
            "Select 3 to 6 publicly traded comparable peers for valuation comparison. "
            "Return JSON {\"peers\": [{\"ticker\": str, \"rationale\": str}]}. "
            "Do NOT invent valuation multiples; multiples are fetched programmatically."
        )
        res = llm_chat(sys_p, json.dumps({"ticker": ticker, "industry": industry, "synced_candidates": candidates}, ensure_ascii=False), "t5_peers")
        if isinstance(res, dict) and isinstance(res.get("peers"), list):
            cleaned = []
            for p in res["peers"][:8]:
                if isinstance(p, dict) and p.get("ticker"):
                    pt = str(p["ticker"]).strip().upper()
                    if pt and pt != ticker.upper():
                        cleaned.append({"ticker": pt, "rationale": str(p.get("rationale") or "")[:300]})
            if cleaned:
                peers = cleaned
                source = "llm"

    return {
        "task": "T5",
        "source": source,
        "industry": industry,
        "peers": peers,
        "comparison": peer_compare or {"companies": [], "medians": {}, "missing": []},
    }


# ------------------------------------------------------------------ T6: Red-Team / Bear-Case Review

def run_t6_red_team(
    ticker: str,
    calc: dict[str, Any],
    methods_out: Optional[dict[str, Any]] = None,
    *,
    std: Optional[dict[str, Any]] = None,
    llm_chat: Optional[LlmChat] = None,
) -> dict[str, Any]:
    """Adversarial audit of DCF & multi-method assumptions: contradictions, fragility, and missing risks."""
    findings: list[dict[str, str]] = []
    fv = calc.get("fair_value_per_share")
    price = calc.get("price")
    disc = calc.get("discount") or {}
    wacc = (disc.get("wacc") or {}).get("value")
    term = calc.get("terminal") or {}
    term_g = term.get("g")
    tv_share = term.get("tv_share_of_ev")
    proj = calc.get("projections") or []

    if fv and price and price > 0:
        ratio = float(fv) / float(price)
        if ratio > 1.8:
            findings.append({
                "severity": "high",
                "topic": "valuation_vs_market",
                "detail": f"Base DCF ({fv:.2f}) is {ratio:.1f}× market price ({price:.2f}); market is pricing far lower growth, margins, or terminal multiple.",
            })
        elif ratio < 0.5:
            findings.append({
                "severity": "medium",
                "topic": "valuation_vs_market",
                "detail": f"Base DCF ({fv:.2f}) is only {ratio:.1f}× market price ({price:.2f}); check whether optionality or higher terminal RONIC is priced in.",
            })

    if tv_share is not None and float(tv_share) > 0.70:
        findings.append({
            "severity": "medium",
            "topic": "terminal_dominance",
            "detail": f"Terminal value accounts for {float(tv_share):.1%} of EV (>70%), making fair value highly sensitive to WACC and terminal g.",
        })

    if wacc is not None and term_g is not None and (float(wacc) - float(term_g)) < 0.045:
        mult = 1.0 / max(float(wacc) - float(term_g), 1e-6)
        findings.append({
            "severity": "high",
            "topic": "tight_wacc_spread",
            "detail": f"WACC ({float(wacc):.2%}) − g ({float(term_g):.2%}) = {float(wacc) - float(term_g):.2%}, implying a {mult:.1f}× terminal FCFF multiple.",
        })

    if proj:
        first_rev = float(proj[0].get("revenue") or 0)
        last_rev = float(proj[-1].get("revenue") or 0)
        if first_rev > 0 and last_rev / first_rev > 5.0:
            findings.append({
                "severity": "high",
                "topic": "revenue_compounding",
                "detail": f"Forecast revenue compounds {last_rev / first_rev:.1f}× over the explicit horizon ({first_rev:,.0f}m → {last_rev:,.0f}m); verify addressable market ceiling.",
            })
        avg_da = sum(float(p.get("da") or 0) for p in proj)
        avg_cpx = sum(float(p.get("capex") or 0) for p in proj)
        if avg_cpx > 0 and avg_da > avg_cpx * 1.25:
            findings.append({
                "severity": "high",
                "topic": "da_exceeds_capex",
                "detail": "Cumulative D&A exceeds CapEx by >25% across the forecast; check for omitted IFRS 16 lease renewals or under-investment.",
            })

    if methods_out:
        for w in methods_out.get("warnings") or []:
            findings.append({
                "severity": "high" if w == "financial_fcff_warning" else "medium",
                "topic": w,
                "detail": f"Multi-method warning active: {w}.",
            })
        rdcf = methods_out.get("reverse_dcf")
        if rdcf and rdcf.get("gap_pp") is not None and abs(float(rdcf["gap_pp"])) >= 5.0:
            findings.append({
                "severity": "medium",
                "topic": "reverse_dcf_gap",
                "detail": f"Market-implied 5Y FCF CAGR ({float(rdcf['implied_growth']):.1%}) diverges from model base ({float(rdcf['base_growth']):.1%}) by {float(rdcf['gap_pp']):+.1f}pp.",
            })

    if std:
        fails = (std.get("checks") or {}).get("failures") or []
        if fails:
            findings.append({
                "severity": "medium",
                "topic": "statement_checks",
                "detail": f"{len(fails)} financial-statement check(s) flagged in the standardized layer; review before relying on base-year inputs.",
            })

    source = "rules"
    if llm_chat is not None:
        sys_p = (
            "You are a skeptical short-seller / red-team reviewer auditing a DCF valuation. "
            "Given the model facts and deterministic findings, return JSON "
            "{\"findings\": [{\"severity\": \"high\"|\"medium\"|\"low\", \"topic\": str, \"detail\": str}]} "
            "identifying the sharpest 3-5 contradictions or omitted risks grounded strictly in the provided data."
        )
        res = llm_chat(sys_p, json.dumps({"ticker": ticker, "fv": fv, "price": price, "wacc": wacc, "tv_share": tv_share, "rule_findings": findings}, ensure_ascii=False), "t6_red_team")
        if isinstance(res, dict) and isinstance(res.get("findings"), list):
            extra = [
                {
                    "severity": str(x.get("severity") or "medium").lower() if str(x.get("severity") or "").lower() in ("high", "medium", "low") else "medium",
                    "topic": str(x.get("topic") or "risk")[:60],
                    "detail": str(x.get("detail") or "")[:400],
                }
                for x in res["findings"][:6]
                if isinstance(x, dict) and x.get("detail")
            ]
            if extra:
                # Keep deterministic high-severity findings + LLM insights
                seen_topics = {f["topic"] for f in findings}
                for e in extra:
                    if e["topic"] not in seen_topics:
                        findings.append(e)
                source = "llm"

    high_n = sum(1 for f in findings if f["severity"] == "high")
    risk_level = "high" if high_n >= 2 else ("medium" if (high_n == 1 or len(findings) >= 2) else "low")
    return {
        "task": "T6",
        "source": source,
        "risk_level": risk_level,
        "findings": findings,
    }


# ------------------------------------------------------------------ T7: Valuation Report & Change Attribution

def run_t7_report(
    ticker: str,
    calc: dict[str, Any],
    methods_out: Optional[dict[str, Any]] = None,
    *,
    prev_run: Optional[dict[str, Any]] = None,
    llm_chat: Optional[LlmChat] = None,
) -> dict[str, Any]:
    """Synthesize investment thesis, key risks, multi-method comparison, and valuation change attribution vs prior run."""
    fv = calc.get("fair_value_per_share")
    price = calc.get("price")
    ccy = (calc.get("fx_if_any") or {}).get("pair", "").split("/")[-1] if "/" in str((calc.get("fx_if_any") or {}).get("pair") or "") else (calc.get("currency") or "")
    wacc = ((calc.get("discount") or {}).get("wacc") or {}).get("value")
    term_g = (calc.get("terminal") or {}).get("g")
    composite = (methods_out or {}).get("composite_value") or fv
    upside = ((float(composite) / float(price)) - 1.0) if (composite and price and float(price) > 0) else None

    attribution: Optional[dict[str, Any]] = None
    if prev_run and prev_run.get("composite_value") and composite:
        prev_fv = float(prev_run["composite_value"])
        delta = float(composite) - prev_fv
        pct = delta / prev_fv if prev_fv else None
        drivers: list[str] = []
        if prev_run.get("wacc") and wacc is not None and abs(float(wacc) - float(prev_run["wacc"])) >= 0.0005:
            drivers.append(f"WACC {float(prev_run['wacc']):.2%} → {float(wacc):.2%}")
        if prev_run.get("terminal_g") is not None and term_g is not None and abs(float(term_g) - float(prev_run["terminal_g"])) >= 0.0005:
            drivers.append(f"Terminal g {float(prev_run['terminal_g']):.2%} → {float(term_g):.2%}")
        if prev_run.get("price") and price and abs(float(price) - float(prev_run["price"])) / max(float(prev_run["price"]), 1e-6) >= 0.01:
            drivers.append(f"Market price {float(prev_run['price']):.2f} → {float(price):.2f}")
        attribution = {
            "from_date": prev_run.get("valuation_date"),
            "to_date": calc.get("valuation_date"),
            "from_value": round(prev_fv, 4),
            "to_value": round(float(composite), 4),
            "delta": round(delta, 4),
            "delta_pct": round(pct, 4) if pct is not None else None,
            "drivers": drivers or ["Updated statements, assumptions, or multi-method weights"],
        }

    thesis = (
        f"{ticker} composite fair value is {ccy} {float(composite):.2f}"
        + (f" ({upside:+.1%} vs market price {ccy} {float(price):.2f})" if upside is not None else "")
        + (f", discounted at WACC {float(wacc):.2%} with terminal growth {float(term_g):.2%}." if (wacc and term_g is not None) else ".")
    )
    risks = [str(g)[:200] for g in (calc.get("data_gaps") or [])[:4]]
    source = "rules"

    if llm_chat is not None:
        sys_p = (
            "Write a concise valuation summary grounded strictly in the provided figures. "
            "Return JSON {\"thesis\": str, \"risks\": [str]}. Never invent numbers outside the input."
        )
        res = llm_chat(
            sys_p,
            json.dumps({"ticker": ticker, "currency": ccy, "composite": composite, "dcf_base": fv, "price": price, "wacc": wacc, "terminal_g": term_g, "attribution": attribution, "gaps": risks}, ensure_ascii=False),
            "t7_report",
        )
        if isinstance(res, dict) and res.get("thesis"):
            thesis = str(res["thesis"])[:800]
            if isinstance(res.get("risks"), list) and res["risks"]:
                risks = [str(x)[:250] for x in res["risks"][:5]]
            source = "llm"

    return {
        "task": "T7",
        "source": source,
        "thesis": thesis,
        "risks": risks,
        "attribution": attribution,
    }


# ------------------------------------------------------------------ T8: Segment & Driver Breakdown (SOTP)

def run_t8_segments(
    ticker: str,
    calc: dict[str, Any],
    *,
    doc_snippets: Optional[list[dict[str, str]]] = None,
    llm_chat: Optional[LlmChat] = None,
) -> dict[str, Any]:
    """Extract business segments and revenue drivers from filing library snippets or model trajectory for SOTP."""
    base_rev = (calc.get("base_year") or {}).get("revenue")
    base_margin = (calc.get("base_year") or {}).get("ebit_margin")
    proj = calc.get("projections") or []
    yr_n_rev = proj[-1].get("revenue") if proj else None
    yr_n_margin = proj[-1].get("ebit_margin") if proj else None

    segments = [
        {
            "name": "Consolidated Operating Business",
            "revenue_share": 1.0,
            "driver": f"Base revenue {base_rev:,.1f}m ({base_margin:.1%} EBIT margin) → horizon {yr_n_rev:,.1f}m ({yr_n_margin:.1%})." if (base_rev and yr_n_rev and base_margin is not None and yr_n_margin is not None) else "Consolidated FCFF revenue and margin trajectory.",
            "source_doc": (doc_snippets[0].get("title") if doc_snippets else "Model base-year financials"),
        }
    ]
    sotp_note = (
        f"Analyzed {len(doc_snippets)} filing snippet(s) from the stock document library."
        if doc_snippets else
        "Sync 10-K/20-F filings into the Document Library to extract detailed footnote segment tables for SOTP."
    )
    source = "rules"

    if llm_chat is not None:
        sys_p = (
            "Extract business segments and revenue growth drivers for SOTP analysis strictly from the provided snippets and model facts. "
            "Return JSON {\"segments\": [{\"name\": str, \"revenue_share\": float|null, \"driver\": str}], \"sotp_note\": str}."
        )
        res = llm_chat(
            sys_p,
            json.dumps({"ticker": ticker, "base_revenue_m": base_rev, "base_margin": base_margin, "snippets": (doc_snippets or [])[:6]}, ensure_ascii=False),
            "t8_segments",
        )
        if isinstance(res, dict) and isinstance(res.get("segments"), list) and res["segments"]:
            parsed = []
            for s in res["segments"][:8]:
                if isinstance(s, dict) and s.get("name"):
                    sh = s.get("revenue_share")
                    try:
                        sh = round(float(sh), 4) if sh is not None else None
                    except (TypeError, ValueError):
                        sh = None
                    parsed.append({
                        "name": str(s["name"])[:120],
                        "revenue_share": sh,
                        "driver": str(s.get("driver") or "")[:300],
                        "source_doc": (doc_snippets[0].get("title") if doc_snippets else "Model & filings"),
                    })
            if parsed:
                segments = parsed
                if res.get("sotp_note"):
                    sotp_note = str(res["sotp_note"])[:400]
                source = "llm"

    return {
        "task": "T8",
        "source": source,
        "segments": segments,
        "sotp_note": sotp_note,
    }
