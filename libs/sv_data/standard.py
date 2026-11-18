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
# Only financial-statement filings feed the standard layer. Other filings tag some of the same concepts with
# different numbers: e.g. a proxy statement (DEF 14A) reports "net income" in its pay-versus-performance table,
# rounded or adjusted (JPMorgan FY2025: 57,000 vs 57,048 in the 10-K), and is filed later, so it would win.
STATEMENT_FORMS = ANNUAL_FORMS | {"10-K405", "10-KT", "10-KT/A", "10-Q", "10-Q/A", "6-K", "6-K/A"}
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
    display: tuple[tuple[str, tuple[str, ...]], ...] = ()   # (statement, item codes in display order)
    totals: frozenset = frozenset()                          # items shown as totals (bold)
    anchors: tuple[tuple[str, tuple[str, ...]], ...] = ()    # (statement, items that pick the period's filing)

    def display_order(self, statement: str) -> list[str]:
        """Items of one statement in display order (the rule order where the file gives none)."""
        given = dict(self.display).get(statement)
        codes = [i.code for i in self.items if i.statement == statement]
        if not given:
            return codes
        return [c for c in given if c in codes] + [c for c in codes if c not in given]

    @property
    def concepts(self) -> set[tuple[str, str]]:
        return {s for i in self.items for r in i.rules for s in r.sources}

    def item(self, code: str) -> Item:
        return next(i for i in self.items if i.code == code)


class RulesError(ValueError):
    pass


# Filing-consistent selection: per period and statement, the filing that reports the anchor item is used for
# every item it reports, so one period never mixes accounting bases (e.g. equity re-reported in a later
# 10-K's equity roll-forward after a restatement, while liabilities at that date are only in the older 10-K).
DEFAULT_ANCHORS = {"balance": ("total_assets", "liabilities_and_equity"),
                   "income": ("net_income_parent", "revenue", "net_income"),
                   "cash_flow": ("cfo",)}


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
    by_code = {i.code: i for i in items}
    display = []
    for st, codes in (d.get("display") or {}).items():
        if st not in STATEMENTS:
            raise RulesError(f"display: unknown statement {st}")
        for c in codes:
            if c not in by_code or by_code[c].statement != st:
                raise RulesError(f"display: {c} is not an item of the {st} statement")
        display.append((st, tuple(codes)))
    totals = frozenset(d.get("totals") or ())
    if not totals <= set(by_code):
        raise RulesError(f"totals: unknown items {sorted(totals - set(by_code))}")
    anchors = []
    for st, codes in (d.get("anchors") or DEFAULT_ANCHORS).items():
        codes = tuple(c for c in codes if c in by_code and by_code[c].statement == st)
        if codes:
            anchors.append((st, codes))
    return Ruleset(str(d["ruleset"]), str(d["version"]), str(d["template"]), tuple(items), tuple(display), totals,
                   tuple(anchors))


@lru_cache(maxsize=8)
def load_ruleset(name: str = "general") -> Ruleset:
    import yaml

    if not re.fullmatch(r"[a-z_]+", name):
        raise RulesError(f"bad ruleset name {name!r}")
    return ruleset_from_dict(yaml.safe_load((RULES_DIR / f"{name}.yaml").read_text(encoding="utf-8")))


def template_for_sic(sic: Optional[str]) -> str:
    """Industry template by SEC SIC code: banks and credit (60xx–61xx), insurance carriers (63xx)."""
    try:
        n = int(str(sic or "").strip())
    except ValueError:
        return "general"
    if 6000 <= n <= 6199:
        return "bank"
    if 6300 <= n <= 6399:          # carriers; 6411 (agents and brokers, e.g. Aon) are not insurers
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
    """A reporting period: a fiscal year (kind "annual"), one of its quarters, or the trailing twelve months."""
    label: str                      # FY2025 (the calendar year the fiscal year ends in) | FY2025 Q1 | TTM
    start: dt.date
    end: dt.date
    kind: str = "annual"            # annual | quarter | ttm
    fy_label: Optional[str] = None  # quarters: their fiscal year
    fy_start: Optional[dt.date] = None
    quarter: Optional[int] = None
    prev_end: Optional[dt.date] = None   # quarters 2–4: end of the previous quarter (for year-to-date derivation)


Period = FiscalYear


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
    inputs: list[str] = field(default_factory=list)          # items a formula used; quarters a TTM sum used
    assumed_zero: list[str] = field(default_factory=list)    # optional formula terms not filed
    parts: list = field(default_factory=list)                # (sign, FactRef) for a derived quarter or TTM
    other_filing: bool = False      # not in the period's anchor filing (that filing does not report this item)


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
    quarters: list = field(default_factory=list)           # fiscal quarters, oldest first
    ttm: Optional[FiscalYear] = None                       # trailing twelve months to the latest quarter

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


QUARTER_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
QUARTER_DAYS = (80, 100)
YTD_DAYS = {1: (80, 100), 2: (170, 190), 3: (260, 285)}


def _near(a: dt.date, b: dt.date) -> bool:
    return abs((a - b).days) <= START_TOLERANCE


def fiscal_quarters(facts: Iterable[FactRef], years: list[FiscalYear]) -> list[FiscalYear]:
    """Quarters of every fiscal year, plus the current one not yet closed by a 10-K.

    Quarter ends come from the year-to-date periods filed in 10-Qs (3, 6 and 9 months from the fiscal-year
    start), falling back to stand-alone three-month periods; Q4 ends with the fiscal year."""
    facts = [f for f in facts if f.form in QUARTER_FORMS and f.period_start is not None]
    fys = list(years)
    if fys:      # the fiscal year in progress
        start = fys[-1].end + dt.timedelta(days=1)
        fys.append(FiscalYear(f"FY{(start + dt.timedelta(days=364)).year}", start, start + dt.timedelta(days=380), kind="open"))
    out: list[FiscalYear] = []
    for fy in fys:
        ytd: dict[int, Counter] = defaultdict(Counter)
        threes: list[FactRef] = []
        for f in facts:
            if not (fy.start < f.period_end <= fy.end):
                continue
            d = f.days or 0
            if _near(f.period_start, fy.start):
                for k, (lo, hi) in YTD_DAYS.items():
                    if lo <= d <= hi:
                        ytd[k][f.period_end] += 1
            if QUARTER_DAYS[0] <= d <= QUARTER_DAYS[1]:
                threes.append(f)
        ends: dict[int, dt.date] = {k: c.most_common(1)[0][0] for k, c in ytd.items() if c}
        for k in (2, 3):         # no year-to-date period: a three-month period right after the previous quarter
            if k not in ends and (k - 1) in ends:
                c = Counter(f.period_end for f in threes if _near(f.period_start, ends[k - 1] + dt.timedelta(days=1)))
                if c:
                    ends[k] = c.most_common(1)[0][0]
        if fy.kind == "annual":
            ends[4] = fy.end
        for k in (1, 2, 3, 4):
            if k not in ends or (k > 1 and (k - 1) not in ends):
                break
            prev = ends.get(k - 1)
            out.append(FiscalYear(f"{fy.label} Q{k}", fy.start if k == 1 else prev + dt.timedelta(days=1), ends[k],
                                  kind="quarter", fy_label=fy.label, fy_start=fy.start, quarter=k, prev_end=prev))
    return out


def _unit_ok(item: Item, unit: str, ccy: Optional[str]) -> bool:
    if item.unit == "money":
        return unit == ccy
    if item.unit == "per_share":
        return unit == f"{ccy}/shares"
    return unit == "shares"


def _in_period(item: Item, f: FactRef, fy: FiscalYear) -> bool:
    if item.period == "instant":
        return f.period_start is None and f.period_end == fy.end
    if f.period_start is None or f.period_end != fy.end or not _near(f.period_start, fy.start):
        return False
    if fy.kind == "quarter":
        return QUARTER_DAYS[0] <= (f.days or 0) <= QUARTER_DAYS[1]
    return True


def _ytd(f: FactRef, fy_start: dt.date, end: dt.date) -> bool:
    """A year-to-date period: from the fiscal-year start to ``end``."""
    return f.period_start is not None and f.period_end == end and _near(f.period_start, fy_start)


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
    """Fiscal years, fiscal quarters and the trailing twelve months, all in one pass.

    Quarters: a filed three-month value is used as is; otherwise a money amount is derived from the
    year-to-date values of the same concept (Q2 = H1 − Q1, Q3 = 9M − H1, Q4 = FY − 9M), method "derived".
    Per-share values and share counts are never derived by subtraction (the share count changes within
    the year). TTM: the sum of the last four quarters for money amounts; for per-share values the usual
    FY + YTD − prior-year YTD; balance-sheet items as at the latest quarter end.

    ``splits`` (from market data) put every per-share value and share count on today's share basis
    (or ``as_of``'s): a value filed before a confirmed split is divided (per share) or multiplied (shares)
    by its ratio. Without ``splits`` nothing is adjusted, and a change between filings by a whole-number
    ratio is reported as ``split_adjusted`` rather than ``restated``."""
    if view not in ("latest", "original"):
        raise ValueError("view must be latest or original")
    facts = [f for f in facts if f.form in STATEMENT_FORMS and (as_of is None or f.filed <= as_of)]
    ccy = reporting_currency(facts)
    years = fiscal_years(facts)
    quarters = fiscal_quarters(facts, years)
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

    def pick(item: Item, cands: list[FactRef], anchor: Optional[str] = None
             ) -> tuple[FactRef, Decimal, Optional[str], Decimal, Decimal, Decimal]:
        """(chosen fact, its adjusted value, change, first value, latest value, split factor).
        With ``anchor`` (an accession), a fact from that filing is preferred."""
        cands = sorted(cands, key=order)
        in_anchor = [f for f in cands if f.accession == anchor] if anchor else []
        chosen = (in_anchor[-1] if in_anchor else cands[-1]) if view == "latest" else (in_anchor[0] if in_anchor else cands[0])
        (v0, u0, _), (v1, u1, _) = adjust(item, cands[0]), adjust(item, cands[-1])
        value, _, k = adjust(item, chosen)
        if splits is None:
            change = change_kind(item, cands[0].value, cands[-1].value)
        else:
            change = None if _same(item.unit, v0, u0, v1, u1) else "restated"
        return chosen, value, change, v0, v1, k

    out: dict[tuple[str, str], StdValue] = {}

    def formula(item: Item, rule: Rule, label: str) -> Optional[StdValue]:
        total, inputs, zero = Decimal(0), [], []
        for t in rule.formula:
            v = out.get((t.item, label))
            if v is None:
                if t.optional:
                    zero.append(t.item)
                    continue
                return None
            total += t.sign * v.value
            inputs.append(t.item)
        if not inputs:
            return None
        return StdValue(item.code, label, total, _unit_label(item, ccy), "formula", rule.id,
                        formula=rule.formula_text, inputs=inputs, assumed_zero=zero)

    def anchor_filings(p: FiscalYear) -> dict[str, str]:
        """statement → accession of the filing chosen for the period's anchor item."""
        out_: dict[str, str] = {}
        for st, codes in ruleset.anchors:
            for code in codes:
                item = ruleset.item(code)
                rule = next((r for r in item.rules if r.applies(p.start)), None)
                for src in (rule.sources if rule else ()):
                    cands = [f for f in by_concept.get(src, ()) if _unit_ok(item, f.unit, ccy) and _in_period(item, f, p)]
                    if cands:
                        out_[st] = pick(item, cands)[0].accession
                        break
                if st in out_:
                    break
        return out_

    def resolve(p: FiscalYear) -> None:
        anchors = anchor_filings(p)
        for item in ruleset.items:
            rule = next((r for r in item.rules if r.applies(p.start)), None)
            if rule is None:
                continue
            got = None
            anchor = anchors.get(item.statement)
            for src in rule.sources:
                pool = [f for f in by_concept.get(src, ()) if _unit_ok(item, f.unit, ccy)]
                cands = [f for f in pool if _in_period(item, f, p)]
                if cands:
                    chosen, value, change, v0, v1, k = pick(item, cands, anchor)
                    got = StdValue(item.code, p.label, value, _unit_label(item, ccy), "concept", rule.id,
                                   concept=f"{src[0]}:{src[1]}", fact=chosen, restated=change == "restated", change=change,
                                   original_value=v0, latest_value=v1, raw_value=chosen.value, split_factor=k,
                                   other_filing=anchor is not None and chosen.accession != anchor)
                    break
                # a quarter without its own three-month value: year-to-date minus the previous year-to-date
                if (p.kind == "quarter" and p.quarter and p.quarter > 1 and item.unit == "money"
                        and item.period == "duration"):
                    cur = [f for f in pool if _ytd(f, p.fy_start, p.end)]
                    prev = [f for f in pool if _ytd(f, p.fy_start, p.prev_end)]
                    if cur and prev:
                        fa, va, ca, *_ = pick(item, cur)
                        fb, vb, cb, *_ = pick(item, prev)
                        change = "restated" if "restated" in (ca, cb) else None
                        got = StdValue(item.code, p.label, va - vb, _unit_label(item, ccy), "derived", rule.id,
                                       concept=f"{src[0]}:{src[1]}", restated=change == "restated", change=change,
                                       parts=[(1, fa), (-1, fb)])
                        break
                    # no previous year-to-date filed: year-to-date minus the earlier quarters (e.g. Q4 = FY − Q1 − Q2 − Q3)
                    earlier = [x for x in quarters if x.fy_label == p.fy_label and (x.quarter or 0) < p.quarter]
                    vals = [out.get((item.code, x.label)) for x in earlier]
                    if cur and len(earlier) == p.quarter - 1 and all(v is not None and v.concept == f"{src[0]}:{src[1]}" for v in vals):
                        fa, va, ca, *_ = pick(item, cur)
                        change = "restated" if ca == "restated" or any(v.restated for v in vals) else None
                        got = StdValue(item.code, p.label, va - sum((v.value for v in vals), Decimal(0)), _unit_label(item, ccy),
                                       "derived", rule.id, concept=f"{src[0]}:{src[1]}", restated=change == "restated",
                                       change=change, parts=[(1, fa)], inputs=[x.label for x in earlier])
                        break
            if got is None and rule.formula:
                got = formula(item, rule, p.label)
            if got is not None:
                out[(item.code, p.label)] = got

    for p in years:
        resolve(p)
    for p in quarters:
        resolve(p)

    # trailing twelve months, to the latest quarter end
    ttm = None
    last4 = quarters[-4:]
    if len(last4) == 4 and all(_near(b.start, a.end + dt.timedelta(days=1)) for a, b in zip(last4, last4[1:])):
        q = last4[-1]
        ttm = FiscalYear("TTM", last4[0].start, q.end, kind="ttm", fy_label=q.fy_label, fy_start=q.fy_start, quarter=q.quarter)
        annual = {y.label: y for y in years}
        prev_fy = next((y for y in reversed(years) if y.end < q.fy_start), None) if q.fy_start else None
        for item in ruleset.items:
            rule = next((r for r in item.rules if r.applies(q.start)), None)
            if rule is None:
                continue
            got = None
            if item.period == "instant":
                v = out.get((item.code, q.label))
                if v is not None:
                    got = StdValue(**{**v.__dict__, "fiscal_year": "TTM"})
            elif item.unit == "money":
                vals = [out.get((item.code, x.label)) for x in last4]
                if all(v is not None for v in vals) and rule.sources:
                    got = StdValue(item.code, "TTM", sum((v.value for v in vals), Decimal(0)), _unit_label(item, ccy), "ttm",
                                   rule.id, inputs=[x.label for x in last4],
                                   restated=any(v.restated for v in vals), change="restated" if any(v.restated for v in vals) else None)
                elif rule.formula:
                    got = formula(item, rule, "TTM")
            elif item.unit == "per_share":
                if q.quarter == 4 and q.fy_label in annual:
                    v = out.get((item.code, q.fy_label))
                    if v is not None:
                        got = StdValue(**{**v.__dict__, "fiscal_year": "TTM"})
                elif prev_fy is not None:
                    fy_v = out.get((item.code, prev_fy.label))
                    for src in rule.sources:
                        pool = [f for f in by_concept.get(src, ()) if _unit_ok(item, f.unit, ccy)]
                        cur = [f for f in pool if _ytd(f, q.fy_start, q.end)]
                        prior_end = [f.period_end for f in pool if f.period_start is not None and _near(f.period_start, prev_fy.start)
                                     and abs((f.period_end - (q.end - dt.timedelta(days=364))).days) <= 7]
                        prior = [f for f in pool if prior_end and _ytd(f, prev_fy.start, prior_end[0])]
                        if fy_v is not None and fy_v.method == "concept" and cur and prior:
                            fa, va, *_ = pick(item, cur)
                            fb, vb, *_ = pick(item, prior)
                            got = StdValue(item.code, "TTM", fy_v.value + va - vb, _unit_label(item, ccy), "ttm", rule.id,
                                           concept=f"{src[0]}:{src[1]}", inputs=[prev_fy.label],
                                           parts=[(1, fy_v.fact), (1, fa), (-1, fb)])
                            break
            if got is not None:
                out[(item.code, "TTM")] = got
    return Standardized(ruleset.name, ruleset.version, ruleset.template, ccy, view, as_of, years, out, applied,
                        quarters=quarters, ttm=ttm)
