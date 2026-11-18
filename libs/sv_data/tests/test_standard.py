"""Standard layer: rules load and validate; annual values come from the right concept, filing and period."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import count

import pytest

from libs.sv_data.standard import (FactRef, RulesError, Split, confirmed_splits, fiscal_years, load_ruleset, parse_formula,
                                   ruleset_from_dict, standardize, template_for_sic)

D = dt.date
_ids = count()


def fact(concept, value, start, end, filed, form="10-K", unit="USD", accn=None, tx="us-gaap"):
    return FactRef(id=next(_ids), taxonomy=tx, concept=concept, unit=unit, value=Decimal(str(value)),
                   period_start=start, period_end=end, form=form, accession=accn or f"A-{filed.isoformat()}", filed=filed)


FY24 = (D(2024, 1, 1), D(2024, 12, 31))
FY23 = (D(2023, 1, 1), D(2023, 12, 31))


def test_shipped_ruleset_loads_and_every_formula_uses_items_above_it():
    rs = load_ruleset("general")
    assert rs.template == "general" and rs.version
    codes = [i.code for i in rs.items]
    assert len(codes) == len(set(codes))
    for i, item in enumerate(rs.items):
        for r in item.rules:
            assert all(t.item in codes[:i] for t in r.formula), item.code
    assert ("us-gaap", "Revenues") in rs.concepts


def test_rules_are_validated():
    base = {"ruleset": "t", "version": "1", "template": "general"}
    with pytest.raises(RulesError, match="not defined above"):
        ruleset_from_dict({**base, "items": [
            {"code": "a", "statement": "income", "period": "duration", "unit": "money", "rules": [{"id": "a.1", "formula": "b - c"}]}]})
    with pytest.raises(RulesError, match="taxonomy:Concept"):
        ruleset_from_dict({**base, "items": [
            {"code": "a", "statement": "income", "period": "duration", "unit": "money", "rules": [{"id": "a.1", "sources": ["Revenues"]}]}]})
    with pytest.raises(RulesError, match="statement"):
        ruleset_from_dict({**base, "items": [{"code": "a", "statement": "p&l", "period": "duration", "unit": "money", "rules": []}]})
    with pytest.raises(RulesError):
        parse_formula("a + + b")
    assert [(t.item, t.sign, t.optional) for t in parse_formula("a - b + c?")] == [("a", 1, False), ("b", -1, False), ("c", 1, True)]


def test_fiscal_years_include_52_53_week_years_and_ignore_quarters():
    facts = [fact("Revenues", 1, D(2023, 1, 30), D(2024, 1, 28), D(2024, 3, 1)),          # 364 days
             fact("Revenues", 1, D(2024, 1, 29), D(2025, 1, 26), D(2025, 3, 1)),          # 364 days
             fact("NetIncomeLoss", 1, D(2024, 1, 29), D(2025, 1, 26), D(2025, 3, 1)),
             fact("Revenues", 1, D(2024, 10, 28), D(2025, 1, 26), D(2025, 3, 1)),         # a quarter in the 10-K
             fact("Revenues", 1, D(2024, 1, 29), D(2025, 1, 26), D(2025, 3, 1), form="10-Q")]
    years = fiscal_years(facts)
    assert [(y.label, y.start, y.end) for y in years] == [("FY2024", D(2023, 1, 30), D(2024, 1, 28)),
                                                           ("FY2025", D(2024, 1, 29), D(2025, 1, 26))]


def test_concept_priority_latest_and_original_views_and_restatement():
    rs = load_ruleset("general")
    facts = [fact("Revenues", 100, *FY24, D(2025, 2, 20), accn="K24"),
             fact("Revenues", 90, *FY23, D(2024, 2, 20), accn="K23"),
             fact("Revenues", 91, *FY23, D(2025, 2, 20), accn="K24"),          # restated in the next 10-K
             fact("RevenueFromContractWithCustomerExcludingAssessedTax", 999, *FY24, D(2025, 2, 20))]   # lower priority
    latest = standardize(facts, rs)
    v = latest.get("revenue", "FY2024")
    assert (v.value, v.concept, v.method, v.rule_id) == (Decimal(100), "us-gaap:Revenues", "concept", "revenue.1")
    r23 = latest.get("revenue", "FY2023")
    assert r23.value == Decimal(91) and r23.restated and r23.original_value == Decimal(90) and r23.fact.accession == "K24"
    original = standardize(facts, rs, view="original")
    assert original.get("revenue", "FY2023").value == Decimal(90)
    # point in time: before the 2025 10-K only the first FY2023 number existed, and FY2024 did not
    then = standardize(facts, rs, as_of=D(2024, 12, 31))
    assert then.get("revenue", "FY2023").value == Decimal(90) and not then.get("revenue", "FY2023").restated
    assert then.get("revenue", "FY2024") is None and [y.label for y in then.fiscal_years] == ["FY2023"]


def test_formulas_fill_gaps_and_record_assumed_zero_terms():
    rs = load_ruleset("general")
    end = FY24[1]
    facts = [fact("Revenues", 500, *FY24, D(2025, 2, 20)), fact("CostOfRevenue", 300, *FY24, D(2025, 2, 20)),
             fact("NetIncomeLoss", 80, *FY24, D(2025, 2, 20)),
             fact("StockholdersEquity", 400, None, end, D(2025, 2, 20)),
             fact("LiabilitiesAndStockholdersEquity", 1000, None, end, D(2025, 2, 20)),
             fact("NetCashProvidedByUsedInOperatingActivities", 120, *FY24, D(2025, 2, 20)),
             fact("PaymentsToAcquirePropertyPlantAndEquipment", 45, *FY24, D(2025, 2, 20))]
    s = standardize(facts, rs)
    gp = s.get("gross_profit", "FY2024")
    assert (gp.value, gp.method, gp.inputs) == (Decimal(200), "formula", ["revenue", "cost_of_revenue"])
    ni = s.get("net_income", "FY2024")
    assert ni.value == Decimal(80) and ni.assumed_zero == ["net_income_nci"]
    eq = s.get("total_equity", "FY2024")
    assert eq.value == Decimal(400) and eq.assumed_zero == ["equity_nci"]
    tl = s.get("total_liabilities", "FY2024")
    assert tl.value == Decimal(600) and tl.inputs == ["liabilities_and_equity", "total_equity"]
    assert s.get("free_cash_flow", "FY2024").value == Decimal(75)
    assert s.get("research_development", "FY2024") is None           # not filed, no formula: left empty


def test_units_and_balance_sheet_dates():
    rs = load_ruleset("general")
    end = FY24[1]
    facts = [fact("Revenues", 500, *FY24, D(2025, 2, 20)),
             fact("Revenues", 70, *FY24, D(2025, 2, 20), unit="EUR"),                  # a minority currency
             fact("EarningsPerShareDiluted", "2.94", *FY24, D(2025, 2, 20), unit="USD/shares"),
             fact("WeightedAverageNumberOfDilutedSharesOutstanding", 2450000000, *FY24, D(2025, 2, 20), unit="shares"),
             fact("Assets", 1000, None, end, D(2025, 2, 20)),
             fact("Assets", 999, None, D(2024, 9, 30), D(2024, 11, 1), form="10-Q")]   # a quarter-end, not the year-end
    s = standardize(facts, rs)
    assert s.currency == "USD" and s.get("revenue", "FY2024").value == Decimal(500)
    eps = s.get("eps_diluted", "FY2024")
    assert eps.value == Decimal("2.94") and eps.unit == "USD/shares"
    assert s.get("shares_diluted_weighted", "FY2024").unit == "shares"
    assert s.get("total_assets", "FY2024").value == Decimal(1000)


def test_rules_follow_the_accounting_period():
    rs = ruleset_from_dict({"ruleset": "t", "version": "1", "template": "general", "items": [
        {"code": "operating_income", "statement": "income", "period": "duration", "unit": "money", "rules": [
            {"id": "old", "effective_to": "2026-12-31", "sources": ["ifrs-full:ProfitLossFromOperatingActivities"]},
            {"id": "ifrs18", "effective_from": "2027-01-01", "sources": ["ifrs-full:OperatingProfitLoss"]}]}]})
    facts = [fact("ProfitLossFromOperatingActivities", 10, D(2026, 1, 1), D(2026, 12, 31), D(2027, 3, 1), tx="ifrs-full", unit="EUR", form="20-F"),
             fact("OperatingProfitLoss", 12, D(2027, 1, 1), D(2027, 12, 31), D(2028, 3, 1), tx="ifrs-full", unit="EUR", form="20-F"),
             fact("ProfitLossFromOperatingActivities", 99, D(2027, 1, 1), D(2027, 12, 31), D(2028, 3, 1), tx="ifrs-full", unit="EUR", form="20-F")]
    s = standardize(facts, rs)
    assert (s.get("operating_income", "FY2026").value, s.get("operating_income", "FY2026").rule_id) == (Decimal(10), "old")
    assert (s.get("operating_income", "FY2027").value, s.get("operating_income", "FY2027").rule_id) == (Decimal(12), "ifrs18")


def test_a_stock_split_is_not_called_a_restatement():
    rs = load_ruleset("general")
    facts = [fact("EarningsPerShareDiluted", "11.93", *FY23, D(2024, 2, 21), unit="USD/shares", accn="K23"),
             fact("EarningsPerShareDiluted", "1.19", *FY23, D(2025, 2, 26), unit="USD/shares", accn="K24"),   # after a 10-for-1 split
             fact("WeightedAverageNumberOfDilutedSharesOutstanding", 2494000000, *FY23, D(2024, 2, 21), unit="shares", accn="K23"),
             fact("WeightedAverageNumberOfDilutedSharesOutstanding", 24940000000, *FY23, D(2025, 2, 26), unit="shares", accn="K24"),
             fact("Revenues", 90, *FY23, D(2024, 2, 21), accn="K23"), fact("Revenues", 180, *FY23, D(2025, 2, 26), accn="K24")]
    s = standardize(facts, rs)
    assert s.get("eps_diluted", "FY2023").change == "split_adjusted" and s.get("eps_diluted", "FY2023").value == Decimal("1.19")
    assert s.get("shares_diluted_weighted", "FY2023").change == "split_adjusted"
    assert s.get("revenue", "FY2023").change == "restated"           # money doubling is a restatement, never a split
    one = standardize([fact("Revenues", 90, *FY23, D(2024, 2, 21))], rs)
    assert one.get("revenue", "FY2023").change is None and not one.get("revenue", "FY2023").restated


def _nvda_like():
    """NVIDIA's real pattern: FY2022 EPS was last filed before the June 2024 10-for-1 split, FY2023 before and after."""
    FY22, FY23_ = (D(2021, 2, 1), D(2022, 1, 30)), (D(2022, 1, 31), D(2023, 1, 29))
    return [fact("EarningsPerShareDiluted", "3.85", *FY22, D(2023, 2, 24), unit="USD/shares", accn="K23"),
            fact("EarningsPerShareDiluted", "3.85", *FY22, D(2024, 2, 21), unit="USD/shares", accn="K24"),
            fact("EarningsPerShareDiluted", "1.74", *FY23_, D(2023, 2, 24), unit="USD/shares", accn="K23"),
            fact("EarningsPerShareDiluted", "1.74", *FY23_, D(2024, 2, 21), unit="USD/shares", accn="K24"),
            fact("EarningsPerShareDiluted", "0.17", *FY23_, D(2025, 2, 26), unit="USD/shares", accn="K25"),
            fact("WeightedAverageNumberOfDilutedSharesOutstanding", 2507000000, *FY23_, D(2024, 2, 21), unit="shares", accn="K24"),
            fact("WeightedAverageNumberOfDilutedSharesOutstanding", 25070000000, *FY23_, D(2025, 2, 26), unit="shares", accn="K25"),
            fact("Revenues", 26974, *FY22, D(2023, 2, 24), accn="K23"), fact("Revenues", 26974, *FY23_, D(2024, 2, 21), accn="K24")]


def test_confirmed_splits_put_every_year_on_todays_share_basis():
    rs = load_ruleset("general")
    splits = [Split(D(2024, 6, 10), Decimal(10)), Split(D(2010, 1, 21), Decimal(50))]   # the second is not in the filings
    s = standardize(_nvda_like(), rs, splits=splits)
    assert [x.date for x in s.splits_applied] == [D(2024, 6, 10)]
    fy22, fy23 = s.get("eps_diluted", "FY2022"), s.get("eps_diluted", "FY2023")
    assert fy22.value == Decimal("0.385") and fy22.raw_value == Decimal("3.85") and fy22.split_factor == 10
    assert fy23.value == Decimal("0.17") and fy23.split_factor == 1
    assert fy23.change is None and not fy23.restated          # 1.74 → 0.17 is the split plus rounding, not a restatement
    assert s.get("shares_diluted_weighted", "FY2023").value == Decimal(25070000000)
    assert s.get("revenue", "FY2022").split_factor == 1       # money is never split-adjusted
    # as of before the split nothing is adjusted
    then = standardize(_nvda_like(), rs, as_of=D(2024, 3, 1), splits=splits)
    assert then.splits_applied == [] and then.get("eps_diluted", "FY2022").value == Decimal("3.85")


def test_a_market_split_the_filings_do_not_show_is_ignored():
    """e.g. Berkshire's 2010 split of class B only: its filed per-share numbers never changed by 50."""
    facts = [fact("EarningsPerShareDiluted", "3.10", *FY23, D(2009, 11, 6), unit="USD/shares"),
             fact("EarningsPerShareDiluted", "3.10", *FY23, D(2010, 2, 26), unit="USD/shares")]
    assert confirmed_splits(facts, [Split(D(2010, 1, 21), Decimal(50))]) == []


def test_template_from_sic():
    assert template_for_sic("6331") == "insurance" and template_for_sic("6021") == "bank"
    assert template_for_sic("3674") == "general" and template_for_sic(None) == "general" and template_for_sic("x") == "general"
