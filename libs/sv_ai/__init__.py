"""AI tasks library (2.0.0-beta.2, D4): unified news source interface, T1/T2 suggestions & strict citation validation."""
from __future__ import annotations

from .news import NewsItem, NewsProvider, merge_news
from .pipeline_tasks import (
    default_peer_tickers,
    run_t4_scenarios,
    run_t5_peers,
    run_t6_red_team,
    run_t7_report,
    run_t8_segments,
)
from .suggestions import (
    AllowedSource,
    SuggestionCandidate,
    VerifiedSuggestion,
    generate_t1_suggestions,
    generate_t2_suggestions,
    generate_t7_suggestions,
    validate_suggestions,
)

__all__ = [
    "AllowedSource",
    "NewsItem",
    "NewsProvider",
    "SuggestionCandidate",
    "VerifiedSuggestion",
    "default_peer_tickers",
    "generate_t1_suggestions",
    "generate_t2_suggestions",
    "generate_t7_suggestions",
    "merge_news",
    "run_t4_scenarios",
    "run_t5_peers",
    "run_t6_red_team",
    "run_t7_report",
    "run_t8_segments",
    "validate_suggestions",
]
