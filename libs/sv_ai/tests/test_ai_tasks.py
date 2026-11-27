"""Unit tests for libs/sv_ai: unified news interface, T1/T2 suggestions & 100%-rejection citation gate."""
from __future__ import annotations

from libs.sv_ai import (
    AllowedSource,
    NewsItem,
    SuggestionCandidate,
    generate_t1_suggestions,
    generate_t2_suggestions,
    merge_news,
    validate_suggestions,
)


def test_citation_validator_rejects_100_percent_of_unverified_sources():
    allowed = {
        "sec:1": AllowedSource("sec:1", "NVDA 10-K", "https://www.sec.gov/nvda-10k.htm", "2026-02-26"),
        "bad:nourl": AllowedSource("bad:nourl", "Missing URL", "", "2026-02-26"),
    }
    candidates = [
        SuggestionCandidate("T1", "Valid check", "Matches SEC filing.", "sec:1", {"value": "100"}),
        SuggestionCandidate("T1", "Hallucinated source ID", "Made-up adjustment.", "sec:999", {"value": "999"}),
        SuggestionCandidate("T2", "Missing URL source", "No clickable link.", "bad:nourl"),
        SuggestionCandidate("T2", "Empty detail", "", "sec:1"),
    ]
    verified, rejected = validate_suggestions(candidates, allowed)
    assert len(verified) == 1
    assert rejected == 3
    assert verified[0].source_id == "sec:1"
    assert verified[0].source_url == "https://www.sec.gov/nvda-10k.htm"


def test_t1_reads_checks_layer_and_t2_reads_unified_news_interface():
    std = {
        "years": [{"label": "FY2025"}],
        "rows": [{
            "code": "revenue",
            "cells": [{
                "value": "94193", "original_value": "93000", "change": "restated",
                "form": "10-K", "filed": "2026-02-15",
                "document_url": "https://www.sec.gov/Archives/edgar/data/200406/jnj.htm",
            }],
        }],
        "checks": {
            "failures": [{
                "rule_id": "yahoo_matches", "period": "FY2025", "severity": "warning",
                "values": {"sec": "94193", "yahoo": "91000"}, "difference": "0.0339",
            }],
        },
    }
    t1_list, t1_rej = generate_t1_suggestions("JNJ", std)
    assert t1_rej == 0 and len(t1_list) == 2
    assert all(s.source_url.startswith("https://www.sec.gov/") for s in t1_list)

    # Inject a fabricated LLM candidate → 100% rejected
    t1_llm, rej_llm = generate_t1_suggestions(
        "JNJ", std,
        llm_candidates=lambda fails, allowed: [
            SuggestionCandidate("T1", "Fake AI item", "Should be rejected", "nonexistent:id"),
        ],
    )
    assert t1_llm == [] and rej_llm == 1

    # Unified news merge & T2
    items = merge_news([
        NewsItem("sec:a1", "NVDA", "sec", "Form 8-K Earnings", "2026-11-20", "https://www.sec.gov/8k-1.htm", "Q3 results"),
        NewsItem("yahoo:dup", "NVDA", "yahoo", "Duplicate URL", "2026-11-19", "https://www.sec.gov/8k-1.htm"),
        NewsItem("yahoo:n1", "NVDA", "yahoo", "Blackwell Ramp", "2026-11-21", "https://finance.yahoo.com/news/nvda"),
    ])
    assert [i.id for i in items] == ["yahoo:n1", "sec:a1"]
    t2_list, t2_rej = generate_t2_suggestions("NVDA", items)
    assert t2_rej == 0 and len(t2_list) == 2
    assert all(s.source_url.startswith("https://") for s in t2_list)
