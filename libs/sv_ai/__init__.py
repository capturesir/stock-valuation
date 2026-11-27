"""AI tasks library (2.0.0-beta.2, D4): unified news source interface, T1/T2 suggestions & strict citation validation."""
from __future__ import annotations

from .news import NewsItem, NewsProvider, merge_news
from .suggestions import (
    AllowedSource,
    SuggestionCandidate,
    VerifiedSuggestion,
    generate_t1_suggestions,
    generate_t2_suggestions,
    validate_suggestions,
)

__all__ = [
    "AllowedSource",
    "NewsItem",
    "NewsProvider",
    "SuggestionCandidate",
    "VerifiedSuggestion",
    "generate_t1_suggestions",
    "generate_t2_suggestions",
    "merge_news",
    "validate_suggestions",
]
