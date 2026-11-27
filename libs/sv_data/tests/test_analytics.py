"""Unit tests for DuPont decomposition, earnings quality (Sloan accruals), and peer comparison."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from libs.sv_data.analytics import PeerEntry, dupont_analysis, earnings_quality, peer_comparison
from libs.sv_data.standard import FactRef, load_ruleset, standardize


def _mk_fact(i: int, concept: str, val: int, yr: int, instant: bool = False) -> FactRef:
    end = dt.date(yr, 12, 31)
    start = None if instant else dt.date(yr, 1, 1)
    return FactRef(
        id=i, taxonomy="us-gaap", concept=concept, unit="USD", value=Decimal(val),
        period_start=start, period_end=end, form="10-K", accession=f"0000000001-{str(yr)[-2:]}-000001",
        filed=dt.date(yr + 1, 2, 15),
    )


def test_dupont_3_and_5_factor_identities_and_earnings_quality():
    facts = [
        # FY2023 (prior year balance sheet)
        _mk_fact(1, "Revenues", 800, 2023),
        _mk_fact(2, "OperatingIncomeLoss", 160, 2023),
        _mk_fact(3, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", 150, 2023),
        _mk_fact(4, "NetIncomeLoss", 120, 2023),
        _mk_fact(5, "Assets", 1000, 2023, instant=True),
        _mk_fact(6, "StockholdersEquity", 400, 2023, instant=True),
        _mk_fact(7, "NetCashProvidedByUsedInOperatingActivities", 150, 2023),
        _mk_fact(8, "PaymentsToAcquirePropertyPlantAndEquipment", 30, 2023),
        # FY2024: Rev=1000, EBIT=250, EBT=200, NI=150, Avg Assets=1200, Avg Equity=500, CFO=180, CapEx=40 → FCF=140
        _mk_fact(11, "Revenues", 1000, 2024),
        _mk_fact(12, "OperatingIncomeLoss", 250, 2024),
        _mk_fact(13, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", 200, 2024),
        _mk_fact(14, "NetIncomeLoss", 150, 2024),
        _mk_fact(15, "Assets", 1400, 2024, instant=True),
        _mk_fact(16, "StockholdersEquity", 600, 2024, instant=True),
        _mk_fact(17, "NetCashProvidedByUsedInOperatingActivities", 180, 2024),
        _mk_fact(18, "PaymentsToAcquirePropertyPlantAndEquipment", 40, 2024),
    ]
    std = standardize(facts, load_ruleset("general"))

    dp = dupont_analysis(std)
    assert len(dp) == 2
    fy24 = dp[0]
    assert fy24["period"] == "FY2024"
    assert fy24["avg_basis"] == "average"
    assert fy24["roe"] == 0.3                # 150 / 500
    assert fy24["net_margin"] == 0.15        # 150 / 1000
    assert abs(fy24["asset_turnover"] - 1000 / 1200) < 1e-3
    assert fy24["equity_multiplier"] == 2.4  # 1200 / 500
    # 3-factor product equals ROE
    prod3 = fy24["net_margin"] * fy24["asset_turnover"] * fy24["equity_multiplier"]
    assert abs(prod3 - fy24["roe"]) < 1e-3
    # 5-factor product equals ROE: tax_burden (0.75) * interest_burden (0.8) * ebit_margin (0.25) = 0.15
    assert fy24["tax_burden"] == 0.75
    assert fy24["interest_burden"] == 0.8
    assert fy24["ebit_margin"] == 0.25
    prod5 = fy24["tax_burden"] * fy24["interest_burden"] * fy24["ebit_margin"] * fy24["asset_turnover"] * fy24["equity_multiplier"]
    assert abs(prod5 - fy24["roe"]) < 1e-3

    eq = earnings_quality(std)
    q24 = eq[0]
    assert q24["cfo_to_net_income"] == 1.2          # 180 / 150
    assert abs(q24["fcf_to_net_income"] - 140 / 150) < 1e-3
    assert q24["sloan_accrual_ratio"] == -0.025     # (150 - 180) / 1200
    assert q24["quality_flag"] == "strong"


def test_peer_comparison_medians():
    res = peer_comparison([
        PeerEntry("AAPL", "Apple", "USD", "general", "FY2024", "A", {"gross_margin": 0.46, "roe": 1.5, "sloan_accrual_ratio": -0.03}),
        PeerEntry("MSFT", "Microsoft", "USD", "general", "FY2024", "A", {"gross_margin": 0.70, "roe": 0.37, "sloan_accrual_ratio": -0.05}),
        PeerEntry("NVDA", "NVIDIA", "USD", "general", "FY2025", "A", {"gross_margin": 0.75, "roe": 0.90, "sloan_accrual_ratio": -0.01}),
    ])
    assert len(res["companies"]) == 3
    assert res["medians"]["gross_margin"] == 0.7
    assert res["medians"]["roe"] == 0.9
    assert res["medians"]["sloan_accrual_ratio"] == -0.03
