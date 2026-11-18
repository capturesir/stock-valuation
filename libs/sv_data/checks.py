"""Checks layer: versioned check rules run over standardised statements (roadmap §3.2 ④, §3.6).

Pure functions. Input: a :class:`~libs.sv_data.standard.Standardized` (and, for cross-source checks,
another source's annual figures). Output: one :class:`CheckResult` per rule and period, and a quality
grade per period — A (every applicable check passed), B (a warning failed), C (an error failed).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Mapping, Optional

from .standard import RULES_DIR, FiscalYear, RulesError, Standardized, StdValue, Term, parse_formula

KINDS = ("equal", "le", "ratio", "sign", "jump", "quarters_sum", "continuity", "cross_source")
SEVERITIES = ("error", "warning", "info")
CHECKED_METHODS = ("concept", "derived")      # a formula's own result is never checked against its inputs
MATCH_DAYS = 12                               # another source's year-end may be a month-end, not the 52-week date


@dataclass(frozen=True)
class CheckRule:
    id: str
    kind: str
    severity: str
    tolerance: Decimal = Decimal(0)
    left: Optional[str] = None
    right: tuple[Term, ...] = ()
    item: Optional[str] = None
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    strict: bool = False
    factor: Decimal = Decimal(20)
    items: tuple[str, ...] = ()
    source: Optional[str] = None
    source_rows: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class CheckSet:
    name: str
    version: str
    rules: tuple[CheckRule, ...]


@dataclass
class CheckResult:
    rule_id: str
    period: str
    severity: str
    status: str                       # pass | fail | skip
    values: dict = field(default_factory=dict)   # the numbers compared (exact strings)
    difference: Optional[Decimal] = None          # relative difference, when it applies
    reason: Optional[str] = None                  # why it was skipped


def checkset_from_dict(d: dict) -> CheckSet:
    rules, seen = [], set()
    for r in d.get("rules") or []:
        rid, kind, sev = str(r["id"]), r.get("kind"), r.get("severity")
        if rid in seen:
            raise RulesError(f"check {rid} defined twice")
        if kind not in KINDS:
            raise RulesError(f"{rid}: kind must be one of {KINDS}")
        if sev not in SEVERITIES:
            raise RulesError(f"{rid}: severity must be one of {SEVERITIES}")
        tol = Decimal(str(r.get("tolerance", 0)))
        common = dict(id=rid, kind=kind, severity=sev, tolerance=tol)
        if kind in ("equal", "le"):
            rule = CheckRule(**common, left=r["left"], right=parse_formula(str(r["right"])))
        elif kind == "ratio":
            rule = CheckRule(**common, item=r["item"], numerator=r["numerator"], denominator=r["denominator"])
        elif kind == "sign":
            rule = CheckRule(**common, item=r["item"], strict=bool(r.get("strict")))
        elif kind == "jump":
            rule = CheckRule(**common, item=r["item"], factor=Decimal(str(r.get("factor", 20))))
        elif kind == "quarters_sum":
            rule = CheckRule(**common, items=tuple(r["items"]))
        elif kind == "cross_source":
            rows = tuple((k, tuple(v)) for k, v in (r.get("items") or {}).items())
            rule = CheckRule(**common, source=r["source"], items=tuple(k for k, _ in rows), source_rows=rows)
        else:
            rule = CheckRule(**common)
        rules.append(rule)
        seen.add(rid)
    return CheckSet(str(d["checks"]), str(d["version"]), tuple(rules))


@lru_cache(maxsize=8)
def load_checks(name: str = "checks_general") -> CheckSet:
    import yaml

    if not re.fullmatch(r"[a-z_]+", name):
        raise RulesError(f"bad check set name {name!r}")
    return checkset_from_dict(yaml.safe_load((RULES_DIR / f"{name}.yaml").read_text(encoding="utf-8")))


def _s(v: Decimal) -> str:
    return format(v, "f")


def _rel(a: Decimal, b: Decimal) -> Decimal:
    m = max(abs(a), abs(b))
    return Decimal(0) if m == 0 else abs(a - b) / m


def run_checks(std: Standardized, checks: CheckSet,
               external: Optional[Mapping[str, Mapping[dt.date, Mapping[str, Decimal]]]] = None) -> list[CheckResult]:
    """``external``: other sources' annual figures, {source: {period end: {row name: value}}}."""
    out: list[CheckResult] = []
    periods: list[FiscalYear] = list(std.fiscal_years) + list(std.quarters)
    get = std.get

    def usable(v: Optional[StdValue]) -> bool:
        return v is not None and v.method in CHECKED_METHODS

    for rule in checks.rules:
        if rule.kind == "continuity":
            ys = std.fiscal_years
            for a, b in zip(ys, ys[1:]):
                gap = (b.end - a.end).days
                if gap > 400:
                    out.append(CheckResult(rule.id, b.label, rule.severity, "fail",
                                           {"previous": a.label, "missing_days": str(gap - 365)}))
            continue
        if rule.kind == "quarters_sum":
            for fy in std.fiscal_years:
                qs = [q for q in std.quarters if q.fy_label == fy.label]
                for item in rule.items:
                    vals = [get(item, q.label) for q in qs]
                    total = get(item, fy.label)
                    # a derived Q4 adds up by construction: only meaningful when all four were filed
                    if len(qs) != 4 or not usable(total) or not all(v is not None and v.method == "concept" for v in vals):
                        continue
                    s = sum((v.value for v in vals), Decimal(0))
                    d = _rel(s, total.value)
                    out.append(CheckResult(f"{rule.id}", fy.label, rule.severity, "pass" if d <= rule.tolerance else "fail",
                                           {"item": item, "quarters": _s(s), "year": _s(total.value)}, d))
            continue
        if rule.kind == "cross_source":
            figures = (external or {}).get(rule.source or "")
            for fy in std.fiscal_years:
                row = None
                if figures:
                    near = [d for d in figures if abs((d - fy.end).days) <= MATCH_DAYS]
                    row = figures[near[0]] if near else None
                for item, names in rule.source_rows:
                    v = get(item, fy.label)
                    if v is None:
                        continue
                    if row is None:
                        if figures is not None:
                            out.append(CheckResult(rule.id, fy.label, rule.severity, "skip", {"item": item},
                                                   reason="no_period" if figures else "no_data"))
                        continue
                    other = next((row[n] for n in names if row.get(n) is not None), None)
                    if other is None:
                        out.append(CheckResult(rule.id, fy.label, rule.severity, "skip", {"item": item}, reason="no_row"))
                        continue
                    d = _rel(v.value, other)
                    out.append(CheckResult(rule.id, fy.label, rule.severity, "pass" if d <= rule.tolerance else "fail",
                                           {"item": item, "sec": _s(v.value), rule.source: _s(other)}, d))
            continue

        prev_by_kind: dict[str, FiscalYear] = {}
        for p in periods:
            kind_key = p.kind
            if rule.kind == "equal":
                left = get(rule.left, p.label)
                rights = [(t, get(t.item, p.label)) for t in rule.right]
                if not usable(left) or any(v is None and not t.optional for t, v in rights) \
                        or any(v is not None and not usable(v) for _, v in rights):
                    continue
                r = sum((t.sign * v.value for t, v in rights if v is not None), Decimal(0))
                d = _rel(left.value, r)
                out.append(CheckResult(rule.id, p.label, rule.severity, "pass" if d <= rule.tolerance else "fail",
                                       {"left": _s(left.value), "right": _s(r)}, d))
            elif rule.kind == "le":
                a, b = get(rule.left, p.label), get(rule.right[0].item, p.label) if rule.right else None
                if a is None or b is None:
                    continue
                ok = a.value <= b.value * (1 + rule.tolerance)
                out.append(CheckResult(rule.id, p.label, rule.severity, "pass" if ok else "fail",
                                       {"left": _s(a.value), "right": _s(b.value)}))
            elif rule.kind == "ratio":
                v, n, dn = get(rule.item, p.label), get(rule.numerator, p.label), get(rule.denominator, p.label)
                if not (usable(v) and usable(n) and usable(dn)) or dn.value == 0:
                    continue
                calc = n.value / dn.value
                # the filed figure was rounded (e.g. to the cent) before any split adjustment: allow that rounding
                raw = v.raw_value if v.raw_value is not None else v.value
                exp = raw.as_tuple().exponent
                half = (Decimal(5).scaleb(exp - 1) if isinstance(exp, int) and exp < 0 else Decimal(0)) / abs(v.split_factor or 1)
                d = _rel(v.value, calc)
                ok = abs(v.value - calc) <= half or d <= rule.tolerance
                out.append(CheckResult(rule.id, p.label, rule.severity, "pass" if ok else "fail",
                                       {"filed": _s(v.value), "computed": _s(calc.quantize(Decimal("0.0001")))}, d))
            elif rule.kind == "sign":
                v = get(rule.item, p.label)
                if v is None:
                    continue
                ok = v.value > 0 if rule.strict else v.value >= 0
                out.append(CheckResult(rule.id, p.label, rule.severity, "pass" if ok else "fail", {"value": _s(v.value)}))
            elif rule.kind == "jump":
                v = get(rule.item, p.label)
                prev = prev_by_kind.get(kind_key)
                pv = get(rule.item, prev.label) if prev else None
                if v is not None:
                    prev_by_kind[kind_key] = p
                if v is None or pv is None or pv.value == 0 or v.value == 0:
                    continue
                ratio = abs(v.value / pv.value)
                ok = Decimal(1) / rule.factor <= ratio <= rule.factor
                out.append(CheckResult(rule.id, p.label, rule.severity, "pass" if ok else "fail",
                                       {"value": _s(v.value), "previous": _s(pv.value), "previous_period": prev.label}))
    return out


def grades(results: list[CheckResult]) -> dict[str, str]:
    """A: every applicable check passed; B: a warning failed; C: an error failed. Info never lowers it."""
    out: dict[str, str] = {}
    for r in results:
        if r.status == "skip":
            continue
        g = out.get(r.period, "A")
        if r.status == "fail" and r.severity == "error":
            g = "C"
        elif r.status == "fail" and r.severity == "warning" and g != "C":
            g = "B"
        out[r.period] = g
    return out
