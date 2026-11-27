"""Unified news & announcement source interface (D4, beta.2).

Free providers (SEC filings, HKEX announcements, Yahoo headlines) and future paid providers
implement the same ``NewsProvider`` protocol so upper layers stay completely decoupled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class NewsItem:
    id: str                     # stable source ID, e.g. "sec:0001045810-26-000012" or "yahoo:abc123"
    ticker: str
    source: str                 # sec | hkex | yahoo | custom
    title: str
    published_at: Optional[str] # YYYY-MM-DD or ISO timestamp
    url: str                    # clickable URL (required)
    summary: str = ""
    kind: str = "news"          # filing | announcement | news

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "source": self.source,
            "title": self.title,
            "published_at": self.published_at,
            "url": self.url,
            "summary": self.summary,
            "kind": self.kind,
        }


class NewsProvider(Protocol):
    name: str

    def fetch(self, ticker: str, limit: int = 10) -> list[NewsItem]: ...


def merge_news(items: list[NewsItem], limit: int = 30) -> list[NewsItem]:
    """Deduplicate news items by URL/ID and return newest first."""
    seen: set[str] = set()
    out: list[NewsItem] = []
    for it in sorted(items, key=lambda x: x.published_at or "", reverse=True):
        if not it.url or not it.url.startswith(("http://", "https://", "/")):
            continue
        key = it.url.strip().lower()
        if key in seen or it.id in seen:
            continue
        seen.add(key)
        seen.add(it.id)
        out.append(it)
        if len(out) >= limit:
            break
    return out
