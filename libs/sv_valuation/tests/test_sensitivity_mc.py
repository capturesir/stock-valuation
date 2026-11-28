"""Unit tests for 2D sensitivity heatmaps and Monte Carlo DCF simulator."""
from __future__ import annotations

from libs.sv_valuation import build_sensitivity_and_monte_carlo


def test_sensitivity_matrix_and_monte_carlo_monotonicity():
    calc = {
        "currency": "USD",
        "price": 100.0,
        "shares": 1000.0,
        "enterprise_value": 120000.0,
        "equity_value": 125000.0,
        "discount": {"wacc": {"value": 0.09}},
        "terminal": {"g": 0.025, "ronic": 0.14, "discount_period_tv": 9.5},
        "base_year": {"revenue": 50000.0},
        "projections": [
            {
                "revenue": 50000.0 * (1.08 ** (i + 1)),
                "ebit": 50000.0 * (1.08 ** (i + 1)) * 0.25,
                "nopat": 50000.0 * (1.08 ** (i + 1)) * 0.25 * 0.79,
                "da": 2500.0,
                "capex": 3200.0,
                "dnwc": 300.0,
                "discount_period": i + 0.5,
            }
            for i in range(10)
        ],
    }
    out = build_sensitivity_and_monte_carlo(calc, trials=1000, seed=7)
    assert out is not None
    wg = out["wacc_vs_g"]
    assert len(wg["rows"]) == 5 and len(wg["cols"]) == 5
    # Higher WACC must strictly lower fair value (same g)
    assert wg["cells"][0][2]["fair_value"] > wg["cells"][2][2]["fair_value"] > wg["cells"][4][2]["fair_value"]
    # Higher terminal g must strictly raise fair value (same WACC)
    assert wg["cells"][2][4]["fair_value"] > wg["cells"][2][2]["fair_value"] > wg["cells"][2][0]["fair_value"]

    wm = out["wacc_vs_margin"]
    assert wm["cells"][2][4]["fair_value"] > wm["cells"][2][2]["fair_value"] > wm["cells"][2][0]["fair_value"]

    mc = out["monte_carlo"]
    assert mc["valid_trials"] == 1000
    assert mc["p10"] < mc["p25"] < mc["p50"] < mc["p75"] < mc["p90"]
    assert 0.0 <= mc["prob_above_price"] <= 1.0
    assert 0.0 <= mc["prob_margin_of_safety_20"] <= mc["prob_above_price"]
    assert len(mc["histogram"]) == 12
    assert sum(b["count"] for b in mc["histogram"]) == 1000
