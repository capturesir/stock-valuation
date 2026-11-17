"""Sensitivity grid and engine guards around WACC <= g.

The default grid is WACC ±4pp in 2pp steps. For a low-WACC name (utilities,
telecoms, a heavily levered balance sheet) the lower rows reached WACC <= g,
which divided by zero or shipped negative / exploding "fair values" such as
3,815 and -468 HKD per share into the Excel and the report.
"""
from __future__ import annotations

import copy

import pytest

from src.engine import fcff_engine as E
from src.pipeline.model_builder import MIN_SPREAD, sensitivity_axes


def _all_cells_valid(ws, gs, spread=MIN_SPREAD):
    return all(w - g >= spread - 1e-5 for w in ws for g in gs)


def test_default_grid_unchanged_when_wacc_is_far_from_g():
    ws, gs, note = sensitivity_axes(0.11986, 0.03, 0.04983)
    assert ws == [0.07986, 0.09986, 0.11986, 0.13986, 0.15986]
    assert gs == pytest.approx([0.02, 0.025, 0.03, 0.035, 0.04])
    assert note is None


def test_grid_narrows_when_wacc_is_close_to_g():
    ws, gs, note = sensitivity_axes(0.06, 0.025, 0.03684)
    assert len(ws) == 5 and ws[2] == pytest.approx(0.06)
    assert _all_cells_valid(ws, gs)
    assert note and "收窄" in note


def test_grid_goes_upward_only_when_too_tight_to_centre():
    ws, gs, note = sensitivity_axes(0.031, 0.025, 0.03684)
    assert ws[0] == pytest.approx(0.031) and ws == sorted(ws)
    assert 0.025 in [round(g, 6) for g in gs]
    assert max(gs) <= 0.025 + 1e-12
    assert all(w > g for w in ws for g in gs)
    assert note and "向上" in note


def test_explicit_grid_with_invalid_cells_is_rejected():
    with pytest.raises(ValueError):
        sensitivity_axes(0.08, 0.03, 0.04, waccs=[0.03, 0.08], gs=[0.03])


def _cfg():
    return {
        "discount": {"wacc": {"value": 0.08}},
        "terminal_in": {"method": "value_driver", "g": 0.03, "ronic": 0.12},
        "rows": [{"year": "FY1", "revenue": 100.0, "ebit_margin": 0.2, "tax_rate": 0.2,
                  "da": 5.0, "capex": 6.0, "delta_nwc": 1.0, "discount_period": 0.5}],
        "bridge": [], "shares": 10.0, "fx_rate": 1.0,
    }


def test_engine_runs_normally():
    fair = E.run(_cfg())[5]
    assert fair > 0


@pytest.mark.parametrize("wacc,g", [(0.03, 0.03), (0.025, 0.03)])
def test_engine_refuses_wacc_not_above_g(wacc, g):
    with pytest.raises(E.EngineInputError):
        E.run(_cfg(), wacc=wacc, g=g)


def test_engine_refuses_non_positive_ronic():
    cfg = copy.deepcopy(_cfg())
    cfg["terminal_in"]["ronic"] = 0
    with pytest.raises(E.EngineInputError):
        E.run(cfg)


def test_engine_error_is_a_runtime_error():
    """The CLI and the web job report RuntimeError as a readable message."""
    assert issubclass(E.EngineInputError, RuntimeError)
