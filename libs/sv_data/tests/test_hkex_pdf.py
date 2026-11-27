"""Tests for D11 HKEX Free financial source: Yahoo + HKEX Annual Report PDF extraction & cross-check."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from libs.sv_data.checks import grades, load_checks, run_checks
from libs.sv_data.sources.base import FinancialDataProvider
from libs.sv_data.sources.hkex_free import (
    HkexFreeProvider,
    cross_check_hkex_vs_yahoo,
    detect_currency_and_scale,
    extract_hkex_pdf_facts,
    hk_cik,
    is_hk_ticker,
    parse_yahoo_annual_facts,
)
from libs.sv_data.standard import FactRef, load_ruleset, standardize


def _to_refs(facts):
    return [
        FactRef(
            id=i + 1,
            taxonomy=f.taxonomy,
            concept=f.concept,
            unit=f.unit,
            value=f.value,
            period_start=f.period_start,
            period_end=f.period_end,
            form=f.form,
            accession=f.accession,
            filed=f.filed,
        )
        for i, f in enumerate(facts)
    ]


def test_hk_helpers_and_provider_protocol():
    assert is_hk_ticker("0700.HK")
    assert is_hk_ticker("5.hk")
    assert not is_hk_ticker("NVDA")
    assert hk_cik("0700.HK") == "HK00000700"
    assert hk_cik("5.HK") == "HK00000005"
    p = HkexFreeProvider()
    assert isinstance(p, FinancialDataProvider)
    assert p.supports("0700.HK") and not p.supports("AAPL")


def test_detect_currency_and_scale():
    assert detect_currency_and_scale("Consolidated Income Statement (HK$'000)") == ("HKD", 1000)
    assert detect_currency_and_scale("綜合損益表 單位：人民幣百萬元") == ("CNY", 1_000_000)
    assert detect_currency_and_scale("Consolidated Balance Sheet (US$ million)") == ("USD", 1_000_000)


def test_extract_bilingual_hkex_pdf_and_cross_check_with_yahoo():
    pdf_pages = [
        (
            82,
            """CONSOLIDATED INCOME STATEMENT
For the year ended 31 December 2024
(RMB million)
Note 2024 2023
Revenue 5 660,257 609,015
Cost of sales (311,011) (315,906)
Gross profit 349,246 293,109
Operating profit 208,099 160,074
Profit before income tax 241,485 161,324
Income tax expense (45,018) (43,276)
Profit for the year 196,467 118,048
Profit attributable to equity holders of the Company 194,073 115,216
Basic earnings per share 20.938 12.186
""",
        ),
        (
            85,
            """綜合財務狀況表
於二零二四年十二月三十一日
（單位：人民幣百萬元）
2024 2023
現金及現金等價物 132,519 172,320
流動資產總值 487,600 518,300
資產總值 1,780,995 1,577,246
流動負債總值 412,500 352,157
負債總值 727,099 703,565
本公司權益持有人應佔權益 973,544 808,591
權益總額 1,053,896 873,681
""",
        ),
        (
            88,
            """CONSOLIDATED STATEMENT OF CASH FLOWS
For the year ended 31 December 2024
(RMB million)
2024 2023
Net cash flows generated from operating activities 258,521 221,962
Purchase of property, plant and equipment (76,760) (23,893)
""",
        ),
    ]

    extracted = extract_hkex_pdf_facts("0700.HK", pages=pdf_pages, filed_date=dt.date(2025, 4, 8))
    assert extracted.currency == "CNY"
    assert extracted.scale == 1_000_000
    assert extracted.fiscal_years == [2024, 2023]

    # Yahoo has matching revenue & total assets for 2024, but a 5% discrepancy on Operating Income
    yahoo_annual = {
        "2024-12-31": {
            "Total Revenue": 660_257_000_000,
            "Cost Of Revenue": 311_011_000_000,
            "Gross Profit": 349_246_000_000,
            "Operating Income": 195_000_000_000,   # intentionally ~6.3% off from PDF 208,099M
            "Net Income": 194_073_000_000,
            "Total Assets": 1_780_995_000_000,
            "Total Liabilities Net Minority Interest": 727_099_000_000,
            "Total Equity Gross Minority Interest": 1_053_896_000_000,
            "Operating Cash Flow": 258_521_000_000,
            "Capital Expenditure": -76_760_000_000,
        },
        "2023-12-31": {
            "Total Revenue": 609_015_000_000,
            "Total Assets": 1_577_246_000_000,
            "Total Liabilities Net Minority Interest": 703_565_000_000,
            "Total Equity Gross Minority Interest": 873_681_000_000,
        },
    }
    _, yahoo_facts, _ = parse_yahoo_annual_facts("0700.HK", yahoo_annual, currency="CNY", fetched_date=dt.date(2025, 4, 10))
    cc = cross_check_hkex_vs_yahoo(extracted.facts, yahoo_facts, tolerance=0.01)
    assert cc.compared >= 10
    assert len(cc.discrepancies) == 1
    assert cc.discrepancies[0].concept == "ProfitLossFromOperatingActivities"
    assert cc.discrepancies[0].period_end == "2024-12-31"

    # Standardize combined facts: HKEX-AR (filed 2025-04-15 > YAHOO-AR 2025-03-31) must win for operating_income!
    provider = HkexFreeProvider()
    combined = provider.build_result("0700.HK", yahoo_annual=yahoo_annual, pdf_pages=pdf_pages, currency="CNY")
    assert combined.cross_check is not None
    assert combined.cross_check["discrepancy_count"] == 1

    rs = load_ruleset("general")
    std = standardize(_to_refs(combined.facts), rs)
    assert std.currency == "CNY"
    assert std.get("revenue", "FY2024").value == Decimal("660257000000")
    assert std.get("operating_income", "FY2024").value == Decimal("208099000000")
    assert std.get("operating_income", "FY2024").fact.form == "HKEX-AR"
    assert std.get("free_cash_flow", "FY2024").value == Decimal("181761000000")  # 258,521 - 76,760

    # Accounting checks must pass cleanly (Assets = Liabilities + Equity, Gross Profit = Rev - COGS)
    cs = load_checks("checks_general")
    results = run_checks(std, cs)
    grade = grades(results)
    assert grade["FY2024"] == "A"
    assert grade["FY2023"] == "A"
