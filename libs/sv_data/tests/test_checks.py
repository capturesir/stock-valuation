"""Checks layer: each kind of rule, the formula-skip rule, and grades."""
from __future__ import annotations

from decimal import Decimal

import pytest

from libs.sv_data.checks import checkset_from_dict, grades, load_checks, run_checks
from libs.sv_data.standard import RulesError, Split, load_ruleset, standardize
from libs.sv_data.tests.test_standard import FY23, FY24, D, fact

RS = load_ruleset("general")


def results(facts, external=None, splits=None):
    s = standardize(facts, RS, splits=splits)
    return s, run_checks(s, load_checks(), external)


def by(rs, rule, period):
    return [r for r in rs if r.rule_id == rule and r.period == period]


def bs(total_assets, le, liabilities=None, equity=None, end=FY24[1], filed=D(2025, 2, 20)):
    f = [fact("Assets", total_assets, None, end, filed), fact("LiabilitiesAndStockholdersEquity", le, None, end, filed),
         fact("Revenues", 500, *FY24, filed)]
    if liabilities is not None:
        f.append(fact("Liabilities", liabilities, None, end, filed))
    if equity is not None:
        f.append(fact("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", equity, None, end, filed))
    return f


def test_shipped_checks_load():
    cs = load_checks()
    assert cs.version and {r.id for r in cs.rules} >= {"balance_total", "eps_diluted", "yahoo_matches"}
    with pytest.raises(RulesError):
        checkset_from_dict({"checks": "x", "version": "1", "rules": [{"id": "a", "kind": "guess", "severity": "error"}]})


def test_balance_sheet_reconciles_or_fails_as_an_error():
    _, ok = results(bs(1000, 1000, 600, 400))
    assert [r.status for r in by(ok, "balance_total", "FY2024")] == ["pass"]
    assert [r.status for r in by(ok, "balance_parts", "FY2024")] == ["pass"]
    _, bad = results(bs(1000, 1000, 600, 380))                 # parts 980 vs 1000: 2 % off
    r = by(bad, "balance_parts", "FY2024")[0]
    assert r.status == "fail" and r.severity == "error" and r.difference == Decimal("0.02")
    assert grades(bad)["FY2024"] == "C"


def test_a_formula_is_never_checked_against_its_own_inputs():
    # total liabilities computed as L&E − equity would always reconcile: the check is skipped, not passed
    _, rs = results(bs(1000, 1000, None, 400))
    assert by(rs, "balance_parts", "FY2024") == []


def test_eps_check_allows_rounding_before_a_split():
    filed = D(2014, 3, 1)
    f = [fact("Revenues", 1, *FY23, filed),
         fact("EarningsPerShareDiluted", "0.10", *FY23, filed, unit="USD/shares", accn="K"),
         fact("NetIncomeLoss", 60437000, *FY23, filed),
         fact("WeightedAverageNumberOfDilutedSharesOutstanding", 623786000, *FY23, filed, unit="shares")]
    # NVIDIA FY2014 Q1 pattern: 0.10 filed (rounded from 0.0969), ÷40 after the splits = 0.0025 vs computed 0.00242
    s, rs = results(f, splits=[Split(D(2024, 6, 10), Decimal(40))])
    assert [r.status for r in by(rs, "eps_diluted", "FY2023")] == ["pass"]


def test_eps_check_catches_shares_filed_in_thousands():
    f = [fact("Revenues", 1, *FY24, D(2025, 2, 20)), fact("EarningsPerShareDiluted", "1.31", *FY24, D(2025, 2, 20), unit="USD/shares"),
         fact("NetIncomeLoss", 797645000, *FY24, D(2025, 2, 20)),
         fact("WeightedAverageNumberOfDilutedSharesOutstanding", 606732, *FY24, D(2025, 2, 20), unit="shares")]   # thousands
    _, rs = results(f)
    r = by(rs, "eps_diluted", "FY2024")[0]
    assert r.status == "fail" and r.severity == "warning"


def test_signs_jumps_and_missing_years():
    f = [fact("Revenues", 100, *FY23, D(2024, 2, 20)), fact("Revenues", 2500, *FY24, D(2025, 2, 20)),      # 25× jump
         fact("Revenues", 90, D(2021, 1, 1), D(2021, 12, 31), D(2022, 2, 20)),                            # 2022 missing
         fact("CostOfRevenue", -5, *FY24, D(2025, 2, 20))]
    _, rs = results(f)
    assert by(rs, "revenue_jump", "FY2024")[0].status == "fail"
    assert by(rs, "cost_of_revenue_not_negative", "FY2024")[0].status == "fail"
    assert by(rs, "no_missing_years", "FY2023")[0].status == "fail"
    g = grades(rs)
    assert g["FY2024"] == "B" and g["FY2023"] == "A"          # info (missing year) never lowers a grade


def test_quarters_must_add_up_to_the_year_when_all_four_were_filed():
    q = [(D(2024, 1, 1), D(2024, 3, 31)), (D(2024, 4, 1), D(2024, 6, 30)), (D(2024, 7, 1), D(2024, 9, 30)), (D(2024, 10, 1), D(2024, 12, 31))]
    f = [fact("Revenues", 100, s, e, D(2024, 11, 1), form="10-Q") for s, e in q[:3]]
    f += [fact("Revenues", 200, D(2024, 1, 1), D(2024, 6, 30), D(2024, 8, 1), form="10-Q"),
          fact("Revenues", 300, D(2024, 1, 1), D(2024, 9, 30), D(2024, 11, 1), form="10-Q"),
          fact("Revenues", 140, *q[3], D(2025, 2, 20)), fact("Revenues", 440, *FY24, D(2025, 2, 20))]
    _, rs = results(f)
    r = [x for x in rs if x.rule_id == "quarters_add_up" and x.values.get("item") == "revenue"]
    assert len(r) == 1 and r[0].status == "pass"
    _, rs = results([x for x in f if not (x.period_start == q[3][0])])   # Q4 derived: adds up by construction, so not checked
    assert not [x for x in rs if x.rule_id == "quarters_add_up"]


def test_cross_source_matches_by_year_end_within_days():
    f = bs(1000, 1000) + [fact("NetIncomeLoss", 80, *FY24, D(2025, 2, 20))]
    yahoo = {D(2024, 12, 31): {"Total Revenue": Decimal(500), "Net Income": Decimal(83), "Total Assets": Decimal(1000)}}
    _, rs = results(f, external={"yahoo": yahoo})
    got = {r.values["item"]: r.status for r in by(rs, "yahoo_matches", "FY2024")}
    assert got == {"revenue": "pass", "net_income_parent": "fail", "total_assets": "pass"}
    _, rs = results(f, external={"yahoo": {D(2020, 12, 31): {"Total Revenue": Decimal(1)}}})
    assert {r.status for r in by(rs, "yahoo_matches", "FY2024")} == {"skip"}
    _, rs = results(f)                                          # no second source: no cross-source results at all
    assert by(rs, "yahoo_matches", "FY2024") == []
