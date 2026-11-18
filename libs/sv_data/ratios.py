"""Ratios and growth over standardised statements (roadmap §6). Pure functions, rules from rules/ratios_*.yaml.

Every ratio keeps its inputs, so the page can show how it was computed. A ratio whose inputs are missing
is left out rather than estimated; the first year of data has no averaged ratios (no balance a year earlier).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from .standard import RULES_DIR, FiscalYear, Ruleset, RulesError, Standardized, Term, parse_formula

KINDS = ("pct", "times", "days")
PERIOD_KINDS = ("annual", "quarter", "ttm")
TAX_CAP = Decimal("0.5")


@dataclass(frozen=True)
class Measure:
    id: str
    kind: str
    numerator: tuple[Term, ...]
    denominator: tuple[Term, ...]
    average: bool = False
    average_numerator: bool = False
    after_tax: bool = False
    multiply: Decimal = Decimal(1)
    periods: tuple[str, ...] = PERIOD_KINDS


@dataclass(frozen=True)
class RatioSet:
    name: str
    version: str
    measures: tuple[Measure, ...]
    growth: tuple[str, ...]


@dataclass
class RatioValue:
    id: str
    period: str
    value: Decimal
    inputs: dict = field(default_factory=dict)       # item → value used (averages shown as "avg")


def ratioset_from_dict(d: dict) -> RatioSet:
    out, seen = [], set()
    for m in d.get("measures") or []:
        mid = str(m["id"])
        if mid in seen:
            raise RulesError(f"ratio {mid} defined twice")
        if m.get("kind") not in KINDS:
            raise RulesError(f"{mid}: kind must be one of {KINDS}")
        periods = tuple(m.get("periods") or PERIOD_KINDS)
        if not set(periods) <= set(PERIOD_KINDS):
            raise RulesError(f"{mid}: periods must be among {PERIOD_KINDS}")
        out.append(Measure(mid, m["kind"], parse_formula(str(m["numerator"])), parse_formula(str(m["denominator"])),
                           bool(m.get("average")), bool(m.get("average_numerator")), bool(m.get("after_tax")),
                           Decimal(str(m.get("multiply", 1))), periods))
        seen.add(mid)
    return RatioSet(str(d["ratios"]), str(d["version"]), tuple(out), tuple(d.get("growth") or ()))


@lru_cache(maxsize=8)
def load_ratios(name: str = "ratios_general") -> RatioSet:
    import yaml

    if not re.fullmatch(r"[a-z_]+", name):
        raise RulesError(f"bad ratio set name {name!r}")
    return ratioset_from_dict(yaml.safe_load((RULES_DIR / f"{name}.yaml").read_text(encoding="utf-8")))


def _kind(p: FiscalYear) -> str:
    return "annual" if p.kind == "annual" else p.kind


def year_ago(std: Standardized, p: FiscalYear) -> Optional[FiscalYear]:
    """The comparable period a year earlier: the previous fiscal year, or the same quarter last year.
    For TTM (balances only): the quarter that ended a year before the latest one."""
    pool = std.fiscal_years if p.kind == "annual" else std.quarters
    target = p.end - dt.timedelta(days=364)
    near = [x for x in pool if abs((x.end - target).days) <= 10 and x.end < p.end]
    return near[-1] if near else None


def compute_ratios(std: Standardized, ratios: RatioSet, rules: Ruleset) -> dict[tuple[str, str], RatioValue]:
    instant = {i.code for i in rules.items if i.period == "instant"}
    periods = list(std.fiscal_years) + list(std.quarters) + ([std.ttm] if std.ttm else [])
    out: dict[tuple[str, str], RatioValue] = {}

    def value(code: str, p: FiscalYear, average: bool, inputs: dict) -> Optional[Decimal]:
        v = std.get(code, p.label)
        if v is None:
            return None
        if average and code in instant:
            prev_p = year_ago(std, p)
            prev = std.get(code, prev_p.label) if prev_p else None
            if prev is None:
                return None
            inputs[code] = {"end": format(v.value, "f"), "year_ago": format(prev.value, "f")}
            return (v.value + prev.value) / 2
        inputs[code] = format(v.value, "f")
        return v.value

    def expr(terms: tuple[Term, ...], p: FiscalYear, average: bool, inputs: dict) -> Optional[Decimal]:
        total, any_ = Decimal(0), False
        for t in terms:
            v = value(t.item, p, average, inputs)
            if v is None:
                if t.optional:
                    continue
                return None
            total += t.sign * v
            any_ = True
        return total if any_ else None

    for p in periods:
        for m in ratios.measures:
            if _kind(p) not in m.periods:
                continue
            inputs: dict = {}
            num = expr(m.numerator, p, m.average_numerator, inputs)
            den = expr(m.denominator, p, m.average, inputs)
            if num is None or den is None or den == 0:
                continue
            if m.after_tax:
                tax, pre = std.get("income_tax", p.label), std.get("pretax_income", p.label)
                if tax is None or pre is None or pre.value == 0:
                    continue
                rate = min(max(tax.value / pre.value, Decimal(0)), TAX_CAP)
                inputs["tax_rate"] = format(rate.quantize(Decimal("0.0001")), "f")
                num = num * (1 - rate)
            out[(m.id, p.label)] = RatioValue(m.id, p.label, num / den * m.multiply, inputs)
    return out


def growth(std: Standardized, items: tuple[str, ...]) -> dict[tuple[str, str], Decimal]:
    """Year-on-year growth: each fiscal year against the previous one, each quarter against the same quarter
    a year earlier. Left out when the earlier value is zero or negative (growth is not meaningful then)."""
    out: dict[tuple[str, str], Decimal] = {}
    for p in list(std.fiscal_years) + list(std.quarters):
        prev_p = year_ago(std, p)
        if prev_p is None:
            continue
        for code in items:
            v, prev = std.get(code, p.label), std.get(code, prev_p.label)
            if v is None or prev is None or prev.value <= 0:
                continue
            out[(code, p.label)] = v.value / prev.value - 1
    return out


def cagr(std: Standardized, items: tuple[str, ...], spans: tuple[int, ...] = (3, 5)) -> dict[tuple[str, int], Decimal]:
    """Compound annual growth to the latest fiscal year over 3 and 5 years (both ends must be positive)."""
    out: dict[tuple[str, int], Decimal] = {}
    ys = std.fiscal_years
    if not ys:
        return out
    last = ys[-1]
    for n in spans:
        target = last.end - dt.timedelta(days=round(365.25 * n))
        first = next((y for y in ys if abs((y.end - target).days) <= 15), None)
        if first is None:
            continue
        for code in items:
            a, b = std.get(code, first.label), std.get(code, last.label)
            if a is None or b is None or a.value <= 0 or b.value <= 0:
                continue
            g = (float(b.value) / float(a.value)) ** (1.0 / n) - 1.0
            out[(code, n)] = Decimal(str(round(g, 6)))
    return out
