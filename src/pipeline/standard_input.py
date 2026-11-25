"""Statements for the generic spec from the SEC standard layer (alpha.3 step 10).

The generic spec (``generic_spec.py``) reads annual statements laid out like Yahoo's (row name -> value,
one block per fiscal-year end). For US filers the backend now passes the standardised statements instead
(SEC XBRL, the ruleset, the chosen adjustment plan), and this module lays them out the same way:

* every row the standard layer covers is taken from it (exact filed values, adjusted where the plan says);
* rows it does not cover (pensions, deferred tax, associates, leases …) still come from Yahoo for the
  same fiscal year, and the data gaps say so;
* a fiscal year whose checks found an error (grade C) is left out unless the plan adjusts that year
  (decision: error-level periods never enter a valuation unhandled);
* only fiscal years from the standard layer are used, never a Yahoo-only year.

Pure function: no network, no database.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Optional

from .gaps import Gap

# standard item -> (block, Yahoo row name); sign -1 where Yahoo reports a cash outflow as negative
ROWS: dict[str, tuple[str, str, int]] = {
    "revenue": ("is_a", "Total Revenue", 1),
    "operating_income": ("is_a", "Operating Income", 1),
    "pretax_income": ("is_a", "Pretax Income", 1),
    "income_tax": ("is_a", "Tax Provision", 1),
    "interest_expense": ("is_a", "Interest Expense", 1),
    "shares_diluted_weighted": ("is_a", "Diluted Average Shares", 1),
    "shares_basic_weighted": ("is_a", "Basic Average Shares", 1),
    "cash": ("bs_a", "Cash And Cash Equivalents", 1),
    "receivables": ("bs_a", "Accounts Receivable", 1),
    "inventory": ("bs_a", "Inventory", 1),
    "accounts_payable": ("bs_a", "Accounts Payable", 1),
    "shares_outstanding": ("bs_a", "Ordinary Shares Number", 1),
    "equity_parent": ("bs_a", "Stockholders Equity", 1),
    "total_equity": ("bs_a", "Total Equity Gross Minority Interest", 1),
    "equity_nci": ("bs_a", "Minority Interest", 1),
    "cfo": ("cf_a", "Operating Cash Flow", 1),
    "capex": ("cf_a", "Capital Expenditure", -1),
    "depreciation_amortization": ("cf_a", "Depreciation And Amortization", 1),
}
# Yahoo rows that would be read before (or instead of) a standard row and could disagree with it
SHADOWED = {"is_a": ("Total Operating Income As Reported", "EBIT", "Operating Revenue", "Tax Rate For Calcs",
                     "Reconciled Depreciation", "Interest Expense Non Operating"),
            "bs_a": ("Share Issued", "Cash Cash Equivalents And Short Term Investments", "Total Debt", "Other Short Term Investments"),
            "cf_a": ("Capital Expenditure Reported", "Purchase Of PPE", "Depreciation Amortization Depletion", "Depreciation")}
NEAR_DAYS = 10


def _num(cell: Optional[dict]) -> Optional[float]:
    return float(Decimal(cell["value"])) if cell and cell.get("value") is not None else None


def _yahoo_row(block: dict, end: dt.date) -> dict:
    """Yahoo's row for the fiscal year ending on (or within NEAR_DAYS of) ``end``."""
    for k, row in (block or {}).items():
        try:
            if abs((dt.date.fromisoformat(str(k)[:10]) - end).days) <= NEAR_DAYS:
                return dict(row or {})
        except ValueError:
            continue
    return {}


def to_blocks(std: dict, yahoo_fund: dict) -> tuple[dict, dict, dict, list[str], dict]:
    """(is_a, bs_a, cf_a, data_gaps, source) for ``build_generic_spec`` from a ``standard_statements`` result."""
    gaps: list[str] = []
    years = [(i, y) for i, y in enumerate(std["years"]) if y["kind"] == "annual"]
    rows = {r["code"]: r["cells"] for r in std["rows"]}
    grades = (std.get("checks") or {}).get("grades") or {}
    adjusted_years = {y["label"] for i, y in years
                      for cells in rows.values() if cells[i] and (cells[i].get("adjustment") or {}).get("reason")}
    blocks: dict[str, dict] = {"is_a": {}, "bs_a": {}, "cf_a": {}}
    used, dropped, filled = [], [], set()
    for i, y in years:
        if grades.get(y["label"]) == "C" and y["label"] not in adjusted_years:
            dropped.append(y["label"])
            continue
        end = dt.date.fromisoformat(y["end"])
        key = end.isoformat()
        for b in blocks:
            row = _yahoo_row(yahoo_fund.get(b) or {}, end)
            for name in SHADOWED[b]:
                row.pop(name, None)
            blocks[b][key] = row
        for code, (b, name, sign) in ROWS.items():
            v = _num(rows.get(code, [None] * (i + 1))[i])
            if v is not None:
                blocks[b][key][name] = sign * v
            else:
                blocks[b][key].pop(name, None)
        # derived rows the generic spec reads
        isr, bsr = blocks["is_a"][key], blocks["bs_a"][key]
        if isr.get("Pretax Income") and isr.get("Tax Provision") is not None:
            isr["Tax Rate For Calcs"] = isr["Tax Provision"] / isr["Pretax Income"]
        cash, sti = _num(rows.get("cash", [None] * (i + 1))[i]), _num(rows.get("short_term_investments", [None] * (i + 1))[i])
        if cash is not None:
            bsr["Cash Cash Equivalents And Short Term Investments"] = cash + (sti or 0.0)
        dc, dn = _num(rows.get("debt_current", [None] * (i + 1))[i]), _num(rows.get("debt_noncurrent", [None] * (i + 1))[i])
        if dc is not None or dn is not None:
            debt = (dc or 0.0) + (dn or 0.0)
            lease = bsr.get("Capital Lease Obligations")
            if lease:
                debt += float(lease)
                filled.add("Capital Lease Obligations")
            bsr["Total Debt"] = debt
        for b, name in (("bs_a", n) for n in ("Non Current Deferred Taxes Liabilities", "Long Term Equity Investment",
                                               "Non Current Pension And Other Postretirement Benefit Plans",
                                               "Preferred Stock Equity", "Long Term Provisions", "Investmentin Financial Assets")):
            if blocks[b][key].get(name) is not None:
                filled.add(name)
        used.append(y["label"])
    plan = std.get("plan")
    plan_text = (Gap("plan_central", v=plan["version"]) if plan and plan["kind"] == "central"
                 else Gap("plan_personal", v=plan["version"]) if plan else Gap("plan_none"))
    gaps.append(Gap("sec_standard", ruleset=std["ruleset"], version=std["ruleset_version"], plan=plan_text, years=used or ["—"]))
    if dropped:
        gaps.append(Gap("grade_c_dropped", years=dropped))
    if filled:
        gaps.append(Gap("yahoo_fill", items=sorted(filled)))
    source = {"source": "sec_standard", "ruleset": std["ruleset"], "ruleset_version": std["ruleset_version"],
              "view": std.get("view"), "as_of": std.get("as_of"), "years": used, "dropped": dropped,
              "plan": plan, "grades": {k: v for k, v in grades.items() if k in used}}
    return blocks["is_a"], blocks["bs_a"], blocks["cf_a"], gaps, source
