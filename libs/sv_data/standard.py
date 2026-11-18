"""Standard layer: filed facts → SV standard items per fiscal year (roadmap §3.2 ③, §3.4).

Pure functions. Input is the filed layer (any iterable of :class:`FactRef`), output is one
:class:`StdValue` per (item, fiscal year) with its full lineage: which rule (and ruleset version),
which concept, which filed fact(s), and — for a formula — which items it was computed from.

Choosing a value for a period:

- the rule's ``sources`` are tried in order; the first concept with a filed value for the period wins;
- among that concept's filings for the period, ``view="latest"`` takes the most recently filed value
  (restated, comparable with later years) and ``view="original"`` the first one published;
- ``as_of`` keeps only facts filed on or before that date (point in time: what was known then);
- when no source was filed, the ``formula`` (if any) is computed from items of the same period.

Annual periods only in this step; quarters, Q4 derivation and TTM come next (roadmap alpha.3).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

RULES_DIR = Path(__file__).parent / "rules"
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
ANNUAL_DAYS = (350, 380)            # 52/53-week years included
START_TOLERANCE = 3                 # days: a concept's year may start a day or two off the others
STATEMENTS = ("income", "balance", "cash_flow")
PERIODS = ("duration", "instant")
UNITS = ("money", "per_share", "shares")
TERM = re.compile(r"^([a-z][a-z0-9_]*)(\?)?$")


# ------------------------------------------------------------------ rules

@dataclass(frozen=True)
class Term:
    item: str
    sign: int                       # +1 | -1
    optional: bool


@dataclass(frozen=True)
class Rule:
    id: str
    sources: tuple[tuple[str, str], ...]        # (taxonomy, concept)
    formula: tuple[Term, ...] = ()
    formula_text: Optional[str] = None
    effective_from: Optional[dt.date] = None
    effective_to: Optional[dt.date] = None

    def applies(self, period_start: dt.date) -> bool:
        return ((self.effective_from is None or period_start >= self.effective_from)
                and (self.effective_to is None or period_start <= self.effective_to))


@dataclass(frozen=True)
class Item:
    code: str
    statement: str
    period: str
    unit: str
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Ruleset:
    name: str
    version: str
    template: str
    items: tuple[Item, ...]

    @property
    def concepts(self) -> set[tuple[str, str]]:
        return {s for i in self.items for r in i.rules for s in r.sources}

    def item(self, code: str) -> Item:
        return next(i for i in self.items if i.code == code)


class RulesError(ValueError):
    pass


def _date(v) -> Optional[dt.date]:
    if v is None or isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def parse_formula(text: str) -> tuple[Term, ...]:
    tokens = text.replace("−", "-").split()
    if not tokens or len(tokens) % 2 == 0:
        raise RulesError(f"bad formula: {text!r}")
    terms, sign = [], 1
    for i, tok in enumerate(tokens):
        if i % 2:
            if tok not in "+-" or len(tok) != 1:
                raise RulesError(f"bad operator {tok!r} in {text!r}")
            sign = 1 if tok == "+" else -1
            continue
        m = TERM.match(tok)
        if not m:
            raise RulesError(f"bad term {tok!r} in {text!r}")
        terms.append(Term(m.group(1), sign, bool(m.group(2))))
    return tuple(terms)


def ruleset_from_dict(d: dict) -> Ruleset:
    items: list[Item] = []
    seen: set[str] = set()
    for raw in d.get("items") or []:
        code = raw["code"]
        if code in seen:
            raise RulesError(f"item {code} defined twice")
        for key, allowed in (("statement", STATEMENTS), ("period", PERIODS), ("unit", UNITS)):
            if raw.get(key) not in allowed:
                raise RulesError(f"{code}: {key} must be one of {allowed}")
        rules = []
        for r in raw.get("rules") or []:
            sources = []
            for s in r.get("sources") or []:
                tx, sep, concept = str(s).partition(":")
                if not sep or not concept:
                    raise RulesError(f"{code}: source {s!r} needs taxonomy:Concept")
                sources.append((tx, concept))
            formula = parse_formula(r["formula"]) if r.get("formula") else ()
            for t in formula:
                if t.item not in seen:
                    raise RulesError(f"{code}: formula uses {t.item}, which is not defined above it")
            if not sources and not formula:
                raise RulesError(f"{code}: rule {r.get('id')} has neither sources nor formula")
            rules.append(Rule(id=str(r["id"]), sources=tuple(sources), formula=formula, formula_text=r.get("formula"),
                              effective_from=_date(r.get("effective_from")), effective_to=_date(r.get("effective_to"))))
        if not rules:
            raise RulesError(f"{code}: no rules")
        items.append(Item(code, raw["statement"], raw["period"], raw["unit"], tuple(rules)))
        seen.add(code)
    return Ruleset(str(d["ruleset"]), str(d["version"]), str(d["template"]), tuple(items))


@lru_cache(maxsize=8)
def load_ruleset(name: str = "general") -> Ruleset:
    import yaml

    if not re.fullmatch(r"[a-z_]+", name):
        raise RulesError(f"bad ruleset name {name!r}")
    return ruleset_from_dict(yaml.safe_load((RULES_DIR / f"{name}.yaml").read_text(encoding="utf-8")))


def template_for_sic(sic: Optional[str]) -> str:
    """Industry template by SEC SIC code: banks and credit (60xx–61xx), insurance (63xx–64xx)."""
    try:
        n = int(str(sic or "").strip())
    except ValueError:
        return "general"
    if 6000 <= n <= 6199:
        return "bank"
    if 6300 <= n <= 6499:
        return "insurance"
    return "general"


# ------------------------------------------------------------------ facts in, values out

@dataclass(frozen=True)
class FactRef:
    """A filed fact as the standard layer needs it. ``id`` is whatever the caller uses to find it again."""
    id: object
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    period_start: Optional[dt.date]
    period_end: dt.date
    form: str
    accession: str
    filed: dt.date

    @property
    def days(self) -> Optional[int]:
        return None if self.period_start is None else (self.period_end - self.period_start).days + 1


@dataclass(frozen=True)
class FiscalYear:
    label: str                      # FY2025: the calendar year the fiscal year ends in
    start: dt.date
    end: dt.date


@dataclass
class StdValue:
    item: str
    fiscal_year: str
    value: Decimal
    unit: str
    method: str                     # "concept" | "formula"
    rule_id: str
    concept: Optional[str] = None   # taxonomy:Concept, for method "concept"
    fact: Optional[FactRef] = None  # the filed fact used
    restated: bool = False          # the same period was filed with another value
    change: Optional[str] = None    # "restated" | "split_adjusted" (per-share and share counts after a split)
    original_value: Optional[Decimal] = None       # first and latest filed values, on the same share basis as value
    latest_value: Optional[Decimal] = None
    raw_value: Optional[Decimal] = None            # the chosen fact exactly as filed (before any split adjustment)
    split_factor: Decimal = Decimal(1)             # cumulative ratio of confirmed splits after it was filed
    formula: Optional[str] = None                            # the rule's formula text, for method "formula"
    inputs: list[str] = field(default_factory=list)          # items a formula used
    assumed_zero: list[str] = field(default_factory=list)    # optional formula terms not filed


@dataclass
class Standardized:
    ruleset: str
    version: str
    template: str
    currency: Optional[str]
    view: str
    as_of: Optional[dt.date]
    fiscal_years: list[FiscalYear]
    values: dict[tuple[str, str], StdValue]
    splits_applied: list = field(default_factory=list)     # confirmed splits used for per-share and share counts

    def get(self, item: str, fy: str) -> Optional[StdValue]:
        return self.values.get((item, fy))


def reporting_currency(facts: Iterable[FactRef]) -> Optional[str]:
    c = Counter(f.unit for f in facts if re.fullmatch(r"[A-Z]{3}", f.unit))
    return c.most_common(1)[0][0] if c else None


def fiscal_years(facts: Iterable[FactRef]) -> list[FiscalYear]:
    """Annual periods from annual reports: for each year-end, the start most facts agree on."""
    starts: dict[dt.date, Counter] = defaultdict(Counter)
    for f in facts:
        if f.form in ANNUAL_FORMS and f.days is not None and ANNUAL_DAYS[0] <= f.days <= ANNUAL_DAYS[1]:
            starts[f.period_end][f.period_start] += 1
    by_label: dict[str, tuple[int, FiscalYear]] = {}
    for end, c in starts.items():
        start, n = c.most_common(1)[0]
        fy = FiscalYear(f"FY{end.year}", start, end)
        if fy.label not in by_label or n > by_label[fy.label][0]:
            by_label[fy.label] = (n, fy)
    return sorted((fy for _, fy in by_label.values()), key=lambda y: y.end)


def _unit_ok(item: Item, unit: str, ccy: Optional[str]) -> bool:
    if item.unit == "money":
        return unit == ccy
    if item.unit == "per_share":
        return unit == f"{ccy}/shares"
    return unit == "shares"


def _in_period(item: Item, f: FactRef, fy: FiscalYear) -> bool:
    if item.period == "instant":
        return f.period_start is None and f.period_end == fy.end
    return (f.period_start is not None and f.period_end == fy.end
            and abs((f.period_start - fy.start).days) <= START_TOLERANCE)


def change_kind(item: Item, first: Decimal, last: Decimal) -> Optional[str]:
    """Why a period's value changed between filings. For per-share values and share counts, a change by a
    whole-number ratio (2, 3, 4, 10, 20 …, either way) is a stock split, not a restatement."""
    if first == last:
        return None
    if item.unit in ("per_share", "shares") and first and last:
        r = abs(first / last)
        r = r if r >= 1 else 1 / r
        n = r.to_integral_value()
        if n >= 2 and abs(r - n) <= Decimal("0.02") * n:
            return "split_adjusted"
    return "restated"


def _unit_label(item: Item, ccy: Optional[str]) -> str:
    return {"money": ccy or "", "per_share": f"{ccy}/shares", "shares": "shares"}[item.unit]


@dataclass(frozen=True)
class Split:
    date: dt.date
    ratio: Decimal                  # 10 for a 10-for-1 split; 0.1 for a 1-for-10 reverse split


def _half_ulp(v: Decimal) -> Decimal:
    """Half of the last reported digit: 1.19 → 0.005. Values are rounded when filed, so a per-share number
    adjusted from 11.93 (÷10 = 1.193) still matches a later 1.19."""
    exp = v.as_tuple().exponent
    return Decimal(5).scaleb(exp - 1) if isinstance(exp, int) and exp < 0 else Decimal("0.5")


SHARES_TOLERANCE = Decimal("0.005")             # share counts are often rounded to thousands


def _same(unit: str, a: Decimal, ua: Decimal, b: Decimal, ub: Decimal) -> bool:
    if unit == "per_share":
        return abs(a - b) <= ua + ub
    if unit == "shares":
        return abs(a - b) <= SHARES_TOLERANCE * max(abs(a), abs(b))
    return a == b


def confirmed_splits(facts: Iterable[FactRef], splits: Iterable[Split]) -> list[Split]:
    """Keep the splits the filings themselves confirm: some period's per-share value or share count was filed
    before the split date and again after it, changed by the split ratio. This guards against a market split
    that does not apply to the filer's reported shares (e.g. a split of one share class only)."""
    groups: dict[tuple, list[FactRef]] = defaultdict(list)
    for f in facts:
        if f.unit == "shares" or f.unit.endswith("/shares"):
            groups[(f.taxonomy, f.concept, f.unit, f.period_start, f.period_end)].append(f)
    out = []
    for sp in splits:
        if sp.ratio in (0, 1):
            continue
        ok = False
        for key, fs in groups.items():
            before = [f for f in fs if f.filed < sp.date]
            after = [f for f in fs if f.filed >= sp.date]
            if not before or not after:
                continue
            b = max(before, key=lambda f: f.filed).value
            a = min(after, key=lambda f: f.filed).value
            if not a or not b:
                continue
            if key[2] == "shares":
                ok = abs(a - b * sp.ratio) <= SHARES_TOLERANCE * abs(b * sp.ratio)
            else:
                ok = abs(b - a * sp.ratio) <= _half_ulp(b) + abs(sp.ratio) * _half_ulp(a)
            if ok:
                break
        if ok:
            out.append(sp)
    return sorted(out, key=lambda s: s.date)


def standardize(facts: Iterable[FactRef], ruleset: Ruleset, view: str = "latest",
                as_of: Optional[dt.date] = None, splits: Optional[Iterable[Split]] = None) -> Standardized:
    """``splits`` (from market data) put every per-share value and share count on today's share basis
    (or ``as_of``'s): a value filed before a confirmed split is divided (per share) or multiplied (shares)
    by its ratio. Without ``splits`` nothing is adjusted, and a change between filings by a whole-number
    ratio is reported as ``split_adjusted`` rather than ``restated``."""
    if view not in ("latest", "original"):
        raise ValueError("view must be latest or original")
    facts = [f for f in facts if as_of is None or f.filed <= as_of]
    ccy = reporting_currency(facts)
    years = fiscal_years(facts)
    applied = confirmed_splits(facts, [s for s in (splits or []) if as_of is None or s.date <= as_of])
    by_concept: dict[tuple[str, str], list[FactRef]] = defaultdict(list)
    for f in facts:
        by_concept[(f.taxonomy, f.concept)].append(f)
    order = (lambda f: (f.filed, f.accession))

    def factor(f: FactRef) -> Decimal:
        x = Decimal(1)
        for sp in applied:
            if f.filed < sp.date:
                x *= sp.ratio
        return x

    def adjust(item: Item, f: FactRef) -> tuple[Decimal, Decimal, Decimal]:
        """(adjusted value, its rounding uncertainty, factor)."""
        k = factor(f) if item.unit in ("per_share", "shares") else Decimal(1)
        if item.unit == "per_share":
            return f.value / k, _half_ulp(f.value) / abs(k), k
        if item.unit == "shares":
            return f.value * k, Decimal(0), k
        return f.value, Decimal(0), k

    out: dict[tuple[str, str], StdValue] = {}
    for fy in years:
        for item in ruleset.items:
            rule = next((r for r in item.rules if r.applies(fy.start)), None)
            if rule is None:
                continue
            got = None
            for src in rule.sources:
                cands = [f for f in by_concept.get(src, ()) if _unit_ok(item, f.unit, ccy) and _in_period(item, f, fy)]
                if not cands:
                    continue
                cands.sort(key=order)
                chosen = cands[-1] if view == "latest" else cands[0]
                (v0, u0, _), (v1, u1, _) = adjust(item, cands[0]), adjust(item, cands[-1])
                value, _, k = adjust(item, chosen)
                if splits is None:
                    change = change_kind(item, cands[0].value, cands[-1].value)
                else:
                    change = None if _same(item.unit, v0, u0, v1, u1) else "restated"
                got = StdValue(item.code, fy.label, value, _unit_label(item, ccy), "concept", rule.id,
                               concept=f"{src[0]}:{src[1]}", fact=chosen, restated=change == "restated", change=change,
                               original_value=v0, latest_value=v1, raw_value=chosen.value, split_factor=k)
                break
            if got is None and rule.formula:
                total, inputs, zero, ok = Decimal(0), [], [], True
                for t in rule.formula:
                    v = out.get((t.item, fy.label))
                    if v is None:
                        if t.optional:
                            zero.append(t.item)
                            continue
                        ok = False
                        break
                    total += t.sign * v.value
                    inputs.append(t.item)
                if ok and inputs:
                    got = StdValue(item.code, fy.label, total, _unit_label(item, ccy), "formula", rule.id,
                                   formula=rule.formula_text, inputs=inputs, assumed_zero=zero)
            if got is not None:
                out[(item.code, fy.label)] = got
    return Standardized(ruleset.name, ruleset.version, ruleset.template, ccy, view, as_of, years, out, applied)
