"""Ratios and growth: averages, after-tax ROIC, which periods a ratio applies to, growth and CAGR."""
from __future__ import annotations

from decimal import Decimal

import pytest

from libs.sv_data.ratios import cagr, compute_ratios, growth, load_ratios, ratioset_from_dict
from libs.sv_data.standard import RulesError, load_ruleset, standardize
from libs.sv_data.tests.test_standard import D, fact

RS = load_ruleset("general")
RR = load_ratios("ratios_general")


def years(n=6):
    f = []
    for i in range(n):
        y = 2019 + i
        s, e, filed = D(y, 1, 1), D(y, 12, 31), D(y + 1, 2, 20)
        rev = 100 * (2 ** i)
        f += [fact("Revenues", rev, s, e, filed), fact("GrossProfit", rev // 2, s, e, filed),
              fact("OperatingIncomeLoss", rev // 4, s, e, filed),
              fact("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", rev // 4, s, e, filed),
              fact("IncomeTaxExpenseBenefit", rev // 20, s, e, filed), fact("NetIncomeLoss", rev // 5, s, e, filed),
              fact("StockholdersEquity", 1000 + 100 * i, None, e, filed),
              fact("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", 1000 + 100 * i, None, e, filed),
              fact("Assets", 3000 + 300 * i, None, e, filed)]
    return standardize(f, RS)


def test_shipped_ratio_rules_load():
    for n in ("ratios_general", "ratios_bank", "ratios_insurance"):
        assert load_ratios(n).measures
    with pytest.raises(RulesError):
        ratioset_from_dict({"ratios": "x", "version": "1", "measures": [{"id": "a", "kind": "odd", "numerator": "a", "denominator": "b"}]})


def test_margins_averages_and_after_tax_roic():
    s = years()
    r = compute_ratios(s, RR, RS)
    assert r[("gross_margin", "FY2024")].value == Decimal("0.5")
    roe = r[("roe", "FY2024")]          # 640 / ((1500 + 1400) / 2)
    assert roe.value == Decimal(640) / Decimal(1450)
    assert roe.inputs["equity_parent"] == {"end": "1500", "year_ago": "1400"}
    assert ("roe", "FY2019") not in r                       # no balance a year earlier: left out, not estimated
    roic = r[("roic", "FY2024")]        # 800 × (1 − 160/800) / ((1500 + 1400) / 2)
    assert roic.value == Decimal(800) * (1 - Decimal("0.2")) / Decimal(1450) and roic.inputs["tax_rate"] == "0.2000"


def test_growth_and_cagr():
    s = years()
    g = growth(s, ("revenue", "net_income_parent"))
    assert g[("revenue", "FY2024")] == Decimal(1) and ("revenue", "FY2019") not in g
    c = cagr(s, ("revenue",))
    assert c[("revenue", 3)] == Decimal("1.0") and c[("revenue", 5)] == Decimal("1.0")   # doubling every year


def test_growth_is_left_out_when_the_earlier_value_is_not_positive():
    f = [fact("Revenues", 10, D(2023, 1, 1), D(2023, 12, 31), D(2024, 2, 1)), fact("NetIncomeLoss", -5, D(2023, 1, 1), D(2023, 12, 31), D(2024, 2, 1)),
         fact("Revenues", 12, D(2024, 1, 1), D(2024, 12, 31), D(2025, 2, 1)), fact("NetIncomeLoss", 3, D(2024, 1, 1), D(2024, 12, 31), D(2025, 2, 1))]
    g = growth(standardize(f, RS), ("revenue", "net_income_parent"))
    assert ("revenue", "FY2024") in g and ("net_income_parent", "FY2024") not in g
