"""Tests for unified ValuationMethod protocol, registry, and independent verify() (架構 §2.6)."""
from __future__ import annotations

from libs.sv_valuation.financials import MethodResultRange
from libs.sv_valuation.registry import METHOD_REGISTRY, ValuationMethod, methods_for_template


def test_registry_contains_all_six_methods_and_satisfies_protocol():
    assert set(METHOD_REGISTRY) == {"dcf", "ddm", "ri", "pb_roe", "relative", "reverse_dcf"}
    for m in METHOD_REGISTRY.values():
        assert isinstance(m, ValuationMethod)
        assert len(m.inputs_needed()) > 0
    bank_keys = {m.key for m in methods_for_template("bank")}
    assert {"ri", "ddm", "pb_roe", "dcf", "relative"}.issubset(bank_keys)


def test_independent_verify_passes_valid_and_catches_tampered_results():
    ddm = METHOD_REGISTRY["ddm"]
    res = ddm.value(dps_0=4.0, ke=0.09, g_short=0.06, g_term=0.03, years=5)
    assert res is not None
    assert ddm.verify(res) == []
    tampered = MethodResultRange(method="ddm", bear=res.bear, base=res.base + 15.0, bull=res.bull, assumptions=res.assumptions)
    assert any("mismatch" in err for err in ddm.verify(tampered))

    ri = METHOD_REGISTRY["ri"]
    ri_res = ri.value(bvps_0=100.0, roe=0.14, ke=0.095, payout=0.30, years=5, persistence=0.65)
    assert ri_res is not None
    assert ri.verify(ri_res) == []

    pb = METHOD_REGISTRY["pb_roe"]
    pb_res = pb.value(bvps_0=100.0, roe=0.14, ke=0.095, g_term=0.03)
    assert pb_res is not None
    assert pb.verify(pb_res) == []

    rev = METHOD_REGISTRY["reverse_dcf"]
    rev_res = rev.value(price=100.0, shares=10.0, fcf_0=50.0, wacc=0.09, terminal_g=0.03, net_cash=0.0)
    assert rev_res is not None
    assert rev.verify(rev_res) == []
