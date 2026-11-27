"""Unit tests for libs/sv_valuation: DDM, RI, P/B-ROE, Reverse DCF, Relative, and Football Field."""
from __future__ import annotations

import pytest

from libs.sv_valuation import (
    MethodResultRange,
    MultipleInput,
    build_composite,
    ddm_two_stage,
    pb_roe_justified,
    relative_valuation,
    residual_income,
    solve_implied_growth,
)
from libs.sv_valuation.reverse_dcf import dcf_per_share


def test_ddm_two_stage_matches_gordon_growth_when_short_equals_term():
    # When g_short == g_term == g, two-stage DDM equals Gordon Growth D0*(1+g)/(Ke-g) exactly
    res = ddm_two_stage(dps_0=5.0, ke=0.10, g_short=0.04, g_term=0.04, years=5)
    assert res is not None
    expected = 5.0 * 1.04 / (0.10 - 0.04)
    assert res.base == pytest.approx(expected, rel=1e-6)
    assert res.bear < res.base < res.bull


def test_residual_income_and_pb_roe_on_bank_metrics():
    # Bank with BVPS=100, ROE=14%, Ke=9%, g=3%
    pb = pb_roe_justified(bvps_0=100.0, roe=0.14, ke=0.09, g_term=0.03)
    assert pb is not None
    # Justified P/B = (0.14 - 0.03) / (0.09 - 0.03) = 11/6 = 1.8333...
    assert pb.base == pytest.approx(100.0 * (0.11 / 0.06), rel=1e-4)
    assert pb.bear < pb.base < pb.bull

    ri = residual_income(bvps_0=100.0, roe=0.14, ke=0.09, payout=0.35, persistence=0.70, years=5)
    assert ri is not None
    # ROE > Ke → RI value must exceed Book Value (100)
    assert ri.base > 100.0 and ri.bear < ri.base < ri.bull

    # When ROE == Ke, Residual Income is zero every year → V0 == BVPS exactly
    ri_eq = residual_income(bvps_0=100.0, roe=0.09, ke=0.09, payout=0.35, persistence=0.70, years=5)
    assert ri_eq is not None and ri_eq.base == pytest.approx(100.0, rel=1e-6)


def test_reverse_dcf_solves_market_implied_growth_accurately():
    target_g = 0.14
    mkt_price = dcf_per_share(fcf_0=100.0, growth=target_g, wacc=0.09, terminal_g=0.03, net_cash=50.0, shares=10.0)
    res = solve_implied_growth(
        price=mkt_price, fcf_0=100.0, wacc=0.09, terminal_g=0.03,
        net_cash=50.0, shares=10.0, base_growth=0.08,
    )
    assert res is not None
    assert res.implied_growth == pytest.approx(target_g, abs=1e-4)
    assert res.assessment == "market_more_optimistic"
    assert res.gap_pp == pytest.approx(0.06, abs=1e-4)


def test_relative_valuation_and_composite_routing():
    rel = relative_valuation([
        MultipleInput("pe", company_value=10.0, low_multiple=15.0, mid_multiple=20.0, high_multiple=25.0),
        MultipleInput("ev_ebitda", company_value=200.0, low_multiple=10.0, mid_multiple=12.0, high_multiple=14.0,
                      net_debt=200.0, shares=10.0),
    ])
    assert rel is not None
    # P/E mid = 200; EV/EBITDA mid = (2400-200)/10 = 220 → avg = 210
    assert rel.base == pytest.approx(210.0, rel=1e-4)

    # Bank routing (D5): RI + DDM + P/B-ROE are primary; FCFF DCF carries warning & weight 0
    bank_methods = [
        MethodResultRange("dcf", 110.0, 140.0, 170.0),
        MethodResultRange("ri", 180.0, 210.0, 240.0),
        MethodResultRange("ddm", 170.0, 195.0, 220.0),
        MethodResultRange("pb_roe", 175.0, 200.0, 230.0),
    ]
    comp = build_composite(template="bank", methods=bank_methods, price=190.0)
    assert comp["is_financial"] is True
    assert "financial_fcff_warning" in comp["warnings"]
    assert comp["weights"].get("dcf", 0.0) == 0.0
    assert comp["weights"]["ri"] == pytest.approx(0.45)
    assert comp["weights"]["ddm"] == pytest.approx(0.35)
    assert comp["weights"]["pb_roe"] == pytest.approx(0.20)
    assert comp["composite_value"] == pytest.approx(210 * 0.45 + 195 * 0.35 + 200 * 0.20, rel=1e-4)
    dcf_bar = next(b for b in comp["football_field"] if b["method"] == "dcf")
    assert dcf_bar["warning_code"] == "financial_fcff_warning" and dcf_bar["primary"] is False
