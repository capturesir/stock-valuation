"""Read one filing's XBRL instance: every numeric fact, with its dimensions and company-specific concepts.

SEC companyfacts (filed.py) leaves out two things this adds (roadmap alpha.3 step 8):
- dimensional facts — a value qualified by axes, e.g. EPS per share class
  (us-gaap:StatementClassOfStockAxis = brka:ClassAMember) or a segment;
- company extension concepts — the filer's own elements (e.g. nvda:… for a capex line before FY2021).

Pure function over the instance XML (the "*_htm.xml" EDGAR extracts from an inline XBRL filing, or a
plain instance). Numbers are parsed as Decimal; nil and non-numeric facts are skipped. Uses the standard
library's expat parser, which does not fetch external entities.
"""
from __future__ import annotations

import datetime as dt
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
STANDARD = (("fasb.org/us-gaap", "us-gaap"), ("xbrl.sec.gov/invest", "invest"), ("fasb.org/srt", "srt"), ("xbrl.sec.gov/dei", "dei"),
            ("xbrl.ifrs.org", "ifrs-full"), ("fasb.org/us-types", "us-types"))
SKIP_NS = (XBRLI, XBRLDI, "http://www.xbrl.org/2003/linkbase", "http://www.w3.org/1999/xlink")


@dataclass(frozen=True)
class InstanceFact:
    taxonomy: str                       # us-gaap | dei | srt | ifrs-full | the filer's own prefix (extension)
    concept: str
    value: Decimal
    unit: str                           # USD, shares, USD/shares, pure …
    period_start: Optional[dt.date]     # None for an instant
    period_end: dt.date
    dimensions: tuple = ()              # ((axis, member), …) sorted; empty for a plain (non-dimensional) fact
    decimals: Optional[str] = None
    context: str = ""
    extension: bool = False             # a concept the filer defined itself


@dataclass
class Instance:
    facts: list[InstanceFact] = field(default_factory=list)
    skipped: int = 0                    # nil, non-numeric or unreadable


def _taxonomy(ns: str, prefixes: dict[str, str]) -> tuple[str, bool]:
    for marker, name in STANDARD:
        if marker in ns:
            return name, False
    return prefixes.get(ns, ns.rsplit("/", 1)[-1] or "ext"), True


def _qname(text: str, nsmap: dict[str, str]) -> str:
    """"us-gaap:StatementClassOfStockAxis" → "us-gaap:StatementClassOfStockAxis" with the standard prefix."""
    prefix, _, local = (text or "").strip().partition(":")
    if not local:
        return text.strip()
    ns = nsmap.get(prefix, "")
    name, _ = _taxonomy(ns, {v: k for k, v in nsmap.items()}) if ns else (prefix, True)
    return f"{name}:{local}"


def parse_instance(content: bytes) -> Instance:
    nsmap: dict[str, str] = {}
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(content), events=("start-ns",)):
        nsmap.setdefault(prefix, uri)
    prefixes = {uri: p for p, uri in nsmap.items() if p}
    root = ET.fromstring(content)

    contexts: dict[str, tuple] = {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        p = c.find(f"{{{XBRLI}}}period")
        inst, start, end = (p.findtext(f"{{{XBRLI}}}{k}") for k in ("instant", "startDate", "endDate"))
        try:
            s = None if inst else dt.date.fromisoformat(start.strip()[:10])
            e = dt.date.fromisoformat((inst or end).strip()[:10])
        except (AttributeError, ValueError):
            continue
        dims = []
        for m in c.iter(f"{{{XBRLDI}}}explicitMember"):
            dims.append((_qname(m.get("dimension", ""), nsmap), _qname(m.text or "", nsmap)))
        for m in c.iter(f"{{{XBRLDI}}}typedMember"):
            dims.append((_qname(m.get("dimension", ""), nsmap), "".join(m.itertext()).strip()))
        contexts[c.get("id", "")] = (s, e, tuple(sorted(dims)))

    units: dict[str, str] = {}
    for u in root.iter(f"{{{XBRLI}}}unit"):
        local = lambda q: (q or "").strip().rpartition(":")[2]          # noqa: E731
        div = u.find(f"{{{XBRLI}}}divide")
        if div is not None:
            num = local(div.findtext(f"{{{XBRLI}}}unitNumerator/{{{XBRLI}}}measure"))
            den = local(div.findtext(f"{{{XBRLI}}}unitDenominator/{{{XBRLI}}}measure"))
            units[u.get("id", "")] = f"{num}/{den}"
        else:
            units[u.get("id", "")] = "*".join(local(m.text) for m in u.findall(f"{{{XBRLI}}}measure"))

    out = Instance()
    for el in root:
        if not el.tag.startswith("{"):
            continue
        ns, local = el.tag[1:].split("}", 1)
        if ns in SKIP_NS or el.get("contextRef") is None:
            continue
        ctx, unit = contexts.get(el.get("contextRef")), units.get(el.get("unitRef") or "")
        if ctx is None or unit is None or el.get(XSI_NIL) == "true":
            out.skipped += 1                # no unit: a text or date fact, not a number
            continue
        try:
            value = Decimal((el.text or "").strip())
        except InvalidOperation:
            out.skipped += 1
            continue
        tax, ext = _taxonomy(ns, prefixes)
        out.facts.append(InstanceFact(tax, local, value, unit, ctx[0], ctx[1], ctx[2], el.get("decimals"),
                                      el.get("contextRef"), ext))
    return out
