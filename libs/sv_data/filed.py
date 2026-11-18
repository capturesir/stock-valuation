"""Filed layer: every reported number exactly as the company filed it (roadmap §3.2 ②).

From SEC ``companyfacts``: one :class:`FiledFact` per (concept, unit, period, filing). The same
period appears once per filing that reported it, so a later filing with a different value is a
restatement, and both values are kept — the standard layer decides which to use, and when.

Numbers are parsed as :class:`decimal.Decimal`, never as binary floats, so 0.1 stays 0.1 and a
13-digit dollar amount keeps every digit.

Not in companyfacts (and so not here yet): dimensional facts (segments, axes) and company-specific
extension concepts. Those need the filing's own XBRL instance (a later step).
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator, Optional


@dataclass(frozen=True)
class FiledConcept:
    taxonomy: str               # us-gaap | ifrs-full | dei | srt | invest
    concept: str                # e.g. Revenues
    label: Optional[str]
    description: Optional[str]


@dataclass(frozen=True)
class FiledFact:
    taxonomy: str
    concept: str
    unit: str                   # USD, shares, USD/shares, pure …
    value: Decimal
    period_start: Optional[dt.date]     # None for an instant (balance-sheet date)
    period_end: dt.date
    fy: Optional[int]           # fiscal year of the filing that reported it
    fp: Optional[str]           # FY, Q1, Q2, Q3 (of that filing)
    form: str                   # 10-K, 10-Q, 10-K/A, 20-F, 8-K …
    accession: str              # the filing: 0001045810-24-000029
    filed: dt.date
    frame: Optional[str]        # SEC's calendar frame (CY2023, CY2023Q4I) when this fact is the one SEC picked

    @property
    def key(self) -> tuple:
        return (self.taxonomy, self.concept, self.unit, self.period_start, self.period_end, self.accession)

    @property
    def is_instant(self) -> bool:
        return self.period_start is None

    @property
    def days(self) -> Optional[int]:
        return None if self.period_start is None else (self.period_end - self.period_start).days + 1


@dataclass
class ParsedFacts:
    cik: Optional[str]
    entity_name: Optional[str]
    concepts: list[FiledConcept]
    facts: list[FiledFact]
    duplicates: int = 0         # identical facts listed twice — dropped
    conflicts: int = 0          # same key, different value within one filing — first kept, counted
    skipped: int = 0            # unreadable entries (no end date, no value, no filing)


def _d(v) -> Optional[dt.date]:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _value(v) -> Optional[Decimal]:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, str)):
        try:
            return Decimal(v)
        except ArithmeticError:
            return None
    return None


def iter_entries(doc: dict) -> Iterator[tuple[str, str, dict, str, dict]]:
    for taxonomy, concepts in (doc.get("facts") or {}).items():
        for concept, c in (concepts or {}).items():
            for unit, entries in (c.get("units") or {}).items():
                for e in entries or []:
                    yield taxonomy, concept, c, unit, e


def parse_companyfacts(content: bytes) -> ParsedFacts:
    doc = json.loads(content, parse_float=Decimal)
    from .sources.sec import cik10

    concepts: dict[tuple[str, str], FiledConcept] = {}
    facts: dict[tuple, FiledFact] = {}
    out = ParsedFacts(cik=cik10(doc["cik"]) if doc.get("cik") is not None else None,
                      entity_name=doc.get("entityName"), concepts=[], facts=[])
    for taxonomy, concept, c, unit, e in iter_entries(doc):
        concepts.setdefault((taxonomy, concept), FiledConcept(taxonomy, concept, c.get("label"), c.get("description")))
        end, filed, value = _d(e.get("end")), _d(e.get("filed")), _value(e.get("val"))
        accn = e.get("accn")
        if end is None or filed is None or value is None or not accn:
            out.skipped += 1
            continue
        fy = e.get("fy")
        f = FiledFact(taxonomy=taxonomy, concept=concept, unit=unit, value=value, period_start=_d(e.get("start")),
                      period_end=end, fy=int(fy) if isinstance(fy, (int, Decimal)) else None, fp=e.get("fp") or None,
                      form=str(e.get("form") or ""), accession=str(accn), filed=filed, frame=e.get("frame") or None)
        old = facts.get(f.key)
        if old is None:
            facts[f.key] = f
        elif old.value == f.value:
            out.duplicates += 1
            if f.frame and not old.frame:     # keep SEC's frame tag whichever copy carries it
                facts[f.key] = f
        else:
            out.conflicts += 1
    out.concepts = list(concepts.values())
    out.facts = list(facts.values())
    return out
