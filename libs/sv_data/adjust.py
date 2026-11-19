"""Adjustment layer (D12): apply a plan's adjustments to standardised statements.

An adjustment replaces one standard item's value for one fiscal year or quarter, with a reason and,
optionally, its basis (e.g. a note in the 10-K). Values computed from it follow: formula items of the same
period (e.g. free cash flow = cfo − capex) and the trailing twelve months that sum the adjusted quarter.
The standard value is kept beside the adjusted one; nothing in the filed or standard layers changes.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from .standard import Ruleset, StdValue, Standardized, _unit_label

PERIOD_RE = re.compile(r"FY\d{4}( Q[1-4])?")


@dataclass(frozen=True)
class Adjustment:
    item: str
    period: str                     # FY2024 | FY2024 Q2 (never TTM: it follows from the quarters)
    value: Decimal
    reason: str
    basis: Optional[str] = None
    by: Optional[str] = None
    at: Optional[str] = None


class AdjustmentError(ValueError):
    pass


def validate(adjustments: Iterable[Adjustment], ruleset: Ruleset) -> list[Adjustment]:
    out, seen = [], set()
    codes = {i.code for i in ruleset.items}
    for a in adjustments:
        if a.item not in codes:
            raise AdjustmentError(f"unknown item {a.item!r}")
        if not PERIOD_RE.fullmatch(a.period):
            raise AdjustmentError(f"bad period {a.period!r}")
        if not (a.reason or "").strip():
            raise AdjustmentError(f"{a.item} {a.period}: a reason is required")
        if (a.item, a.period) in seen:
            raise AdjustmentError(f"{a.item} {a.period}: adjusted twice")
        seen.add((a.item, a.period))
        out.append(a)
    return out


def apply(std: Standardized, ruleset: Ruleset, adjustments: Iterable[Adjustment]) -> Standardized:
    """A copy of ``std`` with the adjustments applied. Adjustments for periods not in ``std`` are ignored
    (the caller reports them); the input is not changed."""
    adjs = validate(adjustments, ruleset)
    if not adjs:
        return std
    out = copy.copy(std)
    values = dict(std.values)
    out.values = values
    labels = {p.label for p in std.fiscal_years} | {q.label for q in std.quarters}
    touched: set[str] = set()
    direct: set[tuple[str, str]] = set()
    for a in adjs:
        if a.period not in labels:
            continue
        item = ruleset.item(a.item)
        cur = values.get((a.item, a.period))
        info = {"reason": a.reason, "basis": a.basis, "by": a.by, "at": a.at}
        if cur is not None:
            v = copy.copy(cur)
            v.adjusted_from, v.value, v.adjustment = cur.value, a.value, info
        else:                        # not filed: the adjustment supplies the value
            v = StdValue(a.item, a.period, a.value, _unit_label(item, std.currency), "adjusted", "adjustment",
                         adjustment=info)
        values[(a.item, a.period)] = v
        touched.add(a.period)
        direct.add((a.item, a.period))
    for label in sorted(touched):
        _recompute(values, ruleset, label, direct)
    if std.ttm is not None and touched & {q.label for q in std.quarters}:
        _recompute_ttm(values, ruleset, std.quarters[-1].label)
    return out


def _recompute(values: dict, ruleset: Ruleset, label: str, direct: set) -> None:
    """Formula items of one period, in rule order, from the (adjusted) items above them."""
    changed: set[str] = {i for i, p in direct if p == label}
    for item in ruleset.items:
        v = values.get((item.code, label))
        if v is None or v.method != "formula" or (item.code, label) in direct:
            continue
        rule = next((r for r in item.rules if r.id == v.rule_id), None)
        if rule is None or not any(t.item in changed for t in rule.formula):
            continue
        total = Decimal(0)
        for t in rule.formula:
            x = values.get((t.item, label))
            if x is not None:
                total += t.sign * x.value
        nv = copy.copy(v)
        nv.adjusted_from, nv.value = v.value, total
        nv.adjustment = {"from": sorted(t.item for t in rule.formula if t.item in changed)}
        values[(item.code, label)] = nv
        changed.add(item.code)


def _recompute_ttm(values: dict, ruleset: Ruleset, last_quarter: str) -> None:
    """TTM values that follow an adjusted quarter: four-quarter sums, balance items at the latest quarter
    end, and formulas of those. (Per-share TTM values are not recomputed: adjust the share count instead.)"""
    changed: set[str] = set()
    for item in ruleset.items:
        v = values.get((item.code, "TTM"))
        if v is None:
            continue
        if v.method == "ttm" and v.inputs and not v.parts:          # a sum of four quarters
            qs = [values.get((item.code, q)) for q in v.inputs]
            if any(q is None for q in qs) or not any(q.adjustment for q in qs):
                continue
            total = sum((q.value for q in qs), Decimal(0))
            src = sorted(q.fiscal_year for q in qs if q.adjustment)
        elif v.method == "formula":
            rule = next((r for r in item.rules if r.id == v.rule_id), None)
            if rule is None or not any(t.item in changed for t in rule.formula):
                continue
            total = sum((t.sign * values[(t.item, "TTM")].value for t in rule.formula if (t.item, "TTM") in values), Decimal(0))
            src = sorted(t.item for t in rule.formula if t.item in changed)
        elif item.period == "instant":                               # balance items at the latest quarter end
            q = values.get((item.code, last_quarter))
            if q is None or not q.adjustment:
                continue
            total, src = q.value, [last_quarter]
        else:
            continue
        nv = copy.copy(v)
        nv.adjusted_from, nv.value, nv.adjustment = v.value, total, {"from": src}
        values[(item.code, "TTM")] = nv
        changed.add(item.code)
