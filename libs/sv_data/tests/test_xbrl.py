"""XBRL instance parser: dimensions, company extension concepts, units, exact decimals."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from libs.sv_data.xbrl import parse_instance

FIX = Path(__file__).parent / "fixtures" / "instance.xml"


def test_parse_instance():
    inst = parse_instance(FIX.read_bytes())
    by = {(f.concept, f.dimensions): f for f in inst.facts}
    ni = by[("NetIncomeLoss", ())]
    assert (ni.taxonomy, ni.value, ni.unit, ni.period_start, ni.period_end, ni.extension) == (
        "us-gaap", Decimal("66968000000"), "USD", dt.date(2025, 1, 1), dt.date(2025, 12, 31), False)
    # EPS per share class: the dimensional facts companyfacts leaves out
    a = by[("EarningsPerShareBasic", (("us-gaap:StatementClassOfStockAxis", "brka:ClassAMember"),))]
    b = by[("EarningsPerShareBasic", (("us-gaap:StatementClassOfStockAxis", "brka:ClassBMember"),))]
    assert (a.value, a.unit, b.value) == (Decimal("46437"), "USD/shares", Decimal("30.96"))
    seg = by[("Revenues", (("us-gaap:StatementBusinessSegmentsAxis", "brka:InsuranceMember"),))]
    assert seg.value == Decimal("90000000000")
    # the filer's own concept
    fl = by[("InsuranceFloat", ())]
    assert (fl.taxonomy, fl.extension, fl.period_start, fl.decimals) == ("brka", True, None, "-9")
    assert by[("EntityCommonStockSharesOutstanding", ())].unit == "shares"
    assert inst.skipped == 2                          # the nil goodwill and the text DocumentType
    assert len(inst.facts) == 6
