"""Multi-method valuation library (2.0.0-beta.1, D5).

Pure Python (no FastAPI or SQLAlchemy imports):
- ``financials``: Two-stage DDM, Residual Income (RI), Justified P/B-ROE for banks & insurers
- ``reverse_dcf``: Market-implied growth solver (bisection)
- ``relative``: P/E, P/B, EV/EBITDA, EV/Sales valuation bands
- ``composite``: Industry-aware weighting, financial-sector warning (D5), and Football Field ranges
"""
from __future__ import annotations

from .composite import MethodBand, build_composite, default_weights
from .financials import MethodResultRange, ddm_two_stage, pb_roe_justified, residual_income
from .relative import MultipleInput, relative_valuation
from .reverse_dcf import ReverseDcfResult, solve_implied_growth
from .sensitivity import build_sensitivity_and_monte_carlo

__all__ = [
    "MethodBand",
    "MethodResultRange",
    "MultipleInput",
    "ReverseDcfResult",
    "build_composite",
    "build_sensitivity_and_monte_carlo",
    "ddm_two_stage",
    "default_weights",
    "pb_roe_justified",
    "relative_valuation",
    "residual_income",
    "solve_implied_growth",
]
