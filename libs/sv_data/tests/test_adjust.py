"""Adjustment layer (D12): values replaced, computed values follow, the standard layer is untouched."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import count

import pytest

from libs.sv_data.adjust import Adjustment, AdjustmentError, apply, validate
from libs.sv_data.standard import FactRef, load_ruleset, standardize

D = dt.date
RS = load_ruleset("general")
_ids = count(1)


def f(concept, value, start, end, filed, form="10-K"):
    return FactRef(id=next(_ids), taxonomy="us-gaap", concept=concept, unit="USD", value=Decimal(value), period_start=start,
                   period_end=end, form=form, accession=f"A-{filed}", filed=filed)


def quarters_and_years():
    out = []
    for y in (2023, 2024):
        fy = (D(y, 1, 1), D(y, 12, 31))
        out += [f("NetCashProvidedByUsedInOperatingActivities", 1000, *fy, D(y + 1, 2, 20)),
                f("PaymentsToAcquirePropertyPlantAndEquipment", 200, *fy, D(y + 1, 2, 20))]
    for q, (s, e) in enumerate([(D(2024, 1, 1), D(2024, 3, 31)), (D(2024, 4, 1), D(2024, 6, 30)),
                                (D(2024, 7, 1), D(2024, 9, 30))], 1):
        out += [f("NetCashProvidedByUsedInOperatingActivities", 250, s, e, D(2024, 3 * q + 1, 30), form="10-Q"),
                f("PaymentsToAcquirePropertyPlantAndEquipment", 50, s, e, D(2024, 3 * q + 1, 30), form="10-Q")]
    return standardize(out, RS)


def test_an_adjustment_replaces_the_value_and_formulas_follow():
    std = quarters_and_years()
    assert std.get("free_cash_flow", "FY2024").value == 800
    adj = apply(std, RS, [Adjustment("capex", "FY2024", Decimal(300), "exclude the HQ purchase", "10-K note 7", "Ann")])
    cap, fcf = adj.get("capex", "FY2024"), adj.get("free_cash_flow", "FY2024")
    assert (cap.value, cap.adjusted_from, cap.adjustment["reason"], cap.adjustment["by"]) == (300, 200, "exclude the HQ purchase", "Ann")
    assert (fcf.value, fcf.adjusted_from, fcf.adjustment) == (700, 800, {"from": ["capex"]})
    assert adj.get("free_cash_flow", "FY2023").value == 800 and adj.get("free_cash_flow", "FY2023").adjustment is None
    assert std.get("capex", "FY2024").value == 200 and std.get("capex", "FY2024").adjustment is None     # input unchanged
    assert apply(std, RS, []) is std


def test_a_quarter_adjustment_flows_into_the_trailing_twelve_months():
    std = quarters_and_years()
    assert std.ttm is not None and std.get("cfo", "TTM").value == 1000         # FY2024 Q4 derived: 1000 − 750 = 250
    adj = apply(std, RS, [Adjustment("cfo", "FY2024 Q2", Decimal(150), "working-capital timing")])
    assert adj.get("cfo", "TTM").value == 900 and adj.get("cfo", "TTM").adjustment == {"from": ["FY2024 Q2"]}
    assert adj.get("free_cash_flow", "TTM").value == 700


def test_an_unfiled_value_can_be_supplied_and_unknown_periods_are_ignored():
    std = quarters_and_years()
    adj = apply(std, RS, [Adjustment("research_development", "FY2024", Decimal(5), "from the annual report text"),
                          Adjustment("revenue", "FY1999", Decimal(1), "not shown")])
    rd = adj.get("research_development", "FY2024")
    assert (rd.value, rd.method, rd.adjusted_from) == (5, "adjusted", None)
    assert adj.get("revenue", "FY1999") is None


def test_adjustments_are_validated():
    for bad, msg in ((Adjustment("nope", "FY2024", Decimal(1), "x"), "unknown item"),
                     (Adjustment("revenue", "TTM", Decimal(1), "x"), "bad period"),
                     (Adjustment("revenue", "FY2024", Decimal(1), " "), "reason"),):
        with pytest.raises(AdjustmentError, match=msg):
            validate([bad], RS)
    with pytest.raises(AdjustmentError, match="twice"):
        validate([Adjustment("revenue", "FY2024", Decimal(1), "a"), Adjustment("revenue", "FY2024", Decimal(2), "b")], RS)
