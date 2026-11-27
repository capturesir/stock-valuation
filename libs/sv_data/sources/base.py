"""What every data source shares: HTTP injection, error classes, and the unified FinancialDataProvider protocol (D11)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Protocol, runtime_checkable

from ..filed import FiledConcept, FiledFact
from ..raw import RawDocument

# (url, headers) -> (status, body, response headers); status 0 means a network failure.
# The caller supplies it, with its own pacing, retries and circuit breaker.
HttpGet = Callable[[str, Mapping[str, str]], tuple[int, bytes, Mapping[str, str]]]


class SourceError(RuntimeError):
    """The source could not answer (network, rate limit, server error)."""

    def __init__(self, source: str, url: str, status: int, detail: str = "") -> None:
        super().__init__(f"{source} HTTP {status} for {url}" + (f": {detail}" if detail else ""))
        self.source, self.url, self.status, self.detail = source, url, status, detail


class SourceNotFound(SourceError):
    """The source answered: there is no such document (HTTP 404)."""


@dataclass
class FilingSummary:
    """Minimal filing metadata common across SEC, HKEX, Yahoo and future paid vendors."""
    accession: str
    form: str
    filed: Optional[dt.date] = None
    report_date: Optional[dt.date] = None
    primary_document: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ProviderResult:
    """Output of any FinancialDataProvider implementation (SEC, HKEX Free, GuruFocus, FMP, EODHD …)."""
    provider: str
    ticker: str
    entity_id: str                                # 10-char filer key (SEC CIK or HK00000700)
    entity_name: Optional[str] = None
    currency: Optional[str] = None
    sic: Optional[str] = None
    filings: list[FilingSummary] = field(default_factory=list)
    facts: list[FiledFact] = field(default_factory=list)
    concepts: list[FiledConcept] = field(default_factory=list)
    raw_documents: list[RawDocument] = field(default_factory=list)
    cross_check: Optional[dict] = None


@runtime_checkable
class FinancialDataProvider(Protocol):
    """Unified statement-data source interface (roadmap §3.3, D11).

    Free sources (SEC XBRL, HKEX Free = Yahoo + annual-report PDF) and future paid vendors
    (GuruFocus, FMP, EODHD) implement this protocol without changing upper layers.
    """
    name: str
    markets: tuple[str, ...]

    def supports(self, ticker: str) -> bool: ...
