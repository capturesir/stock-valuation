"""Unified ValuationMethod protocol, registry, and independent verify() checks (docs/財報數據與估值架構.md §2.6)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

from .financials import MethodResultRange, ddm_two_stage, pb_roe_justified, residual_income
from .relative import relative_valuation
from .reverse_dcf import ReverseDcfResult, solve_implied_growth


@runtime_checkable
class ValuationMethod(Protocol):
    """Unified interface for every valuation method in sv_valuation."""
    key: str
    applies_to: frozenset[str]

    def inputs_needed(self) -> list[str]: ...
    def verify(self, result: Any) -> list[str]: ...


def _check_range_monotonic(r: MethodResultRange) -> list[str]:
    issues: list[str] = []
    if r.bear > r.base + 1e-6:
        issues.append(f"{r.method}: bear ({r.bear}) exceeds base ({r.base})")
    if r.base > r.bull + 1e-6:
        issues.append(f"{r.method}: base ({r.base}) exceeds bull ({r.bull})")
    if r.base <= 0:
        issues.append(f"{r.method}: base fair value must be positive (got {r.base})")
    return issues


@dataclass(frozen=True)
class FcffMethod:
    key: str = "dcf"
    applies_to: frozenset[str] = frozenset({"general", "bank", "insurance"})

    def inputs_needed(self) -> list[str]:
        return ["revenue", "operating_income", "income_tax", "depreciation_amortization", "capex", "cfo", "cash", "debt_current", "debt_noncurrent", "shares_diluted_weighted"]

    def verify(self, result: MethodResultRange) -> list[str]:
        return _check_range_monotonic(result)


@dataclass(frozen=True)
class DdmMethod:
    key: str = "ddm"
    applies_to: frozenset[str] = frozenset({"bank", "insurance", "general"})

    def inputs_needed(self) -> list[str]:
        return ["dividends_paid", "shares_diluted_weighted", "net_income_parent", "equity_parent"]

    def value(self, *, dps_0: float, ke: float, g_short: float = 0.05, g_term: float = 0.025, years: int = 5) -> Optional[MethodResultRange]:
        return ddm_two_stage(dps_0=dps_0, ke=ke, g_short=g_short, g_term=g_term, years=years)

    def verify(self, result: MethodResultRange) -> list[str]:
        issues = _check_range_monotonic(result)
        assump = result.assumptions or {}
        if all(k in assump for k in ("dps_0", "ke", "g_short", "g_term")):
            if float(assump["ke"]) <= float(assump["g_term"]):
                issues.append("ddm: ke must exceed terminal growth g_term")
            else:
                recalc = ddm_two_stage(
                    dps_0=float(assump["dps_0"]),
                    ke=float(assump["ke"]),
                    g_short=float(assump["g_short"]),
                    g_term=float(assump["g_term"]),
                    years=int(assump.get("years", 5)),
                )
                if recalc is None or abs(recalc.base - result.base) > max(1e-3, abs(result.base) * 1e-4):
                    issues.append(f"ddm: independent recalculation mismatch ({recalc.base if recalc else None} vs {result.base})")
        return issues


@dataclass(frozen=True)
class ResidualIncomeMethod:
    key: str = "ri"
    applies_to: frozenset[str] = frozenset({"bank", "insurance"})

    def inputs_needed(self) -> list[str]:
        return ["equity_parent", "net_income_parent", "dividends_paid", "shares_diluted_weighted"]

    def value(self, *, bvps_0: float, roe: float, ke: float, payout: float = 0.35, persistence: float = 0.70, years: int = 5) -> Optional[MethodResultRange]:
        return residual_income(bvps_0=bvps_0, roe=roe, ke=ke, payout=payout, persistence=persistence, years=years)

    def verify(self, result: MethodResultRange) -> list[str]:
        issues = _check_range_monotonic(result)
        assump = result.assumptions or {}
        if all(k in assump for k in ("bvps_0", "roe", "ke", "payout")):
            recalc = residual_income(
                bvps_0=float(assump["bvps_0"]),
                roe=float(assump["roe"]),
                ke=float(assump["ke"]),
                payout=float(assump["payout"]),
                persistence=float(assump.get("persistence", 0.70)),
                years=int(assump.get("years", 5)),
            )
            if recalc is None or abs(recalc.base - result.base) > max(1e-3, abs(result.base) * 1e-4):
                issues.append(f"ri: independent recalculation mismatch ({recalc.base if recalc else None} vs {result.base})")
        return issues


@dataclass(frozen=True)
class PbRoeMethod:
    key: str = "pb_roe"
    applies_to: frozenset[str] = frozenset({"bank", "insurance"})

    def inputs_needed(self) -> list[str]:
        return ["equity_parent", "net_income_parent", "shares_diluted_weighted"]

    def value(self, *, bvps_0: float, roe: float, ke: float, g_term: float = 0.025) -> Optional[MethodResultRange]:
        return pb_roe_justified(bvps_0=bvps_0, roe=roe, ke=ke, g_term=g_term)

    def verify(self, result: MethodResultRange) -> list[str]:
        issues = _check_range_monotonic(result)
        assump = result.assumptions or {}
        if all(k in assump for k in ("bvps_0", "roe", "ke", "g_term")):
            recalc = pb_roe_justified(
                bvps_0=float(assump["bvps_0"]),
                roe=float(assump["roe"]),
                ke=float(assump["ke"]),
                g_term=float(assump["g_term"]),
            )
            if recalc is None or abs(recalc.base - result.base) > max(1e-3, abs(result.base) * 1e-4):
                issues.append(f"pb_roe: independent recalculation mismatch ({recalc.base if recalc else None} vs {result.base})")
        return issues


@dataclass(frozen=True)
class RelativeMethod:
    key: str = "relative"
    applies_to: frozenset[str] = frozenset({"general", "bank", "insurance"})

    def inputs_needed(self) -> list[str]:
        return ["net_income_parent", "equity_parent", "revenue", "operating_income", "depreciation_amortization", "shares_diluted_weighted"]

    def value(self, **kwargs) -> Optional[MethodResultRange]:
        return relative_valuation(**kwargs)

    def verify(self, result: MethodResultRange) -> list[str]:
        return _check_range_monotonic(result)


@dataclass(frozen=True)
class ReverseDcfMethod:
    key: str = "reverse_dcf"
    applies_to: frozenset[str] = frozenset({"general"})

    def inputs_needed(self) -> list[str]:
        return ["free_cash_flow", "cash", "debt_current", "debt_noncurrent", "shares_diluted_weighted"]

    def value(self, **kwargs) -> Optional[ReverseDcfResult]:
        return solve_implied_growth(**kwargs)

    def verify(self, result: ReverseDcfResult) -> list[str]:
        issues: list[str] = []
        if not (-0.5 <= float(result.implied_growth) <= 1.0):
            issues.append(f"reverse_dcf: implied_growth out of bounds ({result.implied_growth})")
        if result.price <= 0:
            issues.append(f"reverse_dcf: price must be positive (got {result.price})")
        if result.wacc <= result.terminal_g:
            issues.append(f"reverse_dcf: wacc ({result.wacc}) must exceed terminal_g ({result.terminal_g})")
        if result.base_growth is not None and result.gap_pp is not None:
            expected_gap = (result.implied_growth - result.base_growth) * 100.0
            if abs(expected_gap - result.gap_pp) > 0.2:
                issues.append(f"reverse_dcf: gap_pp mismatch ({result.gap_pp} vs {expected_gap:.2f})")
        return issues


METHOD_REGISTRY: dict[str, ValuationMethod] = {
    "dcf": FcffMethod(),
    "ddm": DdmMethod(),
    "ri": ResidualIncomeMethod(),
    "pb_roe": PbRoeMethod(),
    "relative": RelativeMethod(),
    "reverse_dcf": ReverseDcfMethod(),
}


def methods_for_template(template: str) -> list[ValuationMethod]:
    return [m for m in METHOD_REGISTRY.values() if template in m.applies_to]
