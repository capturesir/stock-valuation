"""HKEX free financial statement source (D11): Yahoo Finance + HKEX Annual Report PDF extraction & cross-check.

Pure Python (no database, no FastAPI, no network calls inside parsers):
- Converts Yahoo Finance annual statements into standard ``ifrs-full`` :class:`FiledFact` records
  (form ``YAHOO-AR``).
- Extracts consolidated income statement, balance sheet, and cash flow figures from HKEX annual-report
  PDFs (English, Traditional Chinese, and Simplified Chinese; auto-detecting currency & unit scales
  such as ``HK$'000``, ``RMB million``, ``人民幣百萬元``, ``千港元``) into ``ifrs-full``
  :class:`FiledFact` records (form ``HKEX-AR``) with exact page provenance.
- Cross-checks PDF facts against Yahoo facts per ``(concept, period_end)`` (roadmap §3.6, default 1%
  tolerance) so discrepancies surface immediately before valuation.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional, Sequence

from ..filed import FiledConcept, FiledFact
from .base import FilingSummary, ProviderResult

HK_TICKER_RE = re.compile(r"^0*(\d{1,5})\.HK$", re.IGNORECASE)


def is_hk_ticker(ticker: str) -> bool:
    return bool(HK_TICKER_RE.match((ticker or "").strip()))


def normalize_hk_code(ticker: str) -> str:
    m = HK_TICKER_RE.match((ticker or "").strip())
    if not m:
        raise ValueError(f"not an HKEX ticker: {ticker!r}")
    return m.group(1).zfill(4)


def hk_cik(ticker: str) -> str:
    """Deterministic 10-character filer key for an HKEX ticker (e.g. ``0700.HK`` → ``HK00000700``)."""
    code = normalize_hk_code(ticker)
    return f"HK{code.zfill(8)}"


def infer_sic_from_sector(sector: Optional[str], industry: Optional[str]) -> Optional[str]:
    """Map Yahoo/HKEX sector & industry strings to SIC codes used by ``template_for_sic``."""
    text = f"{sector or ''} {industry or ''}".lower()
    if any(k in text for k in ("bank", "銀行", "银行", "credit services")):
        return "6021"
    if any(k in text for k in ("insurance", "保險", "保险", "reinsurance")):
        return "6311"
    return None


# ------------------------------------------------------------------ Yahoo → ifrs-full mapping

# (yahoo_row_names_in_priority, concept, period_kind, unit_kind, sign_mode)
# sign_mode:
#   "as_is"  — keep Yahoo sign
#   "abs"    — positive magnitude (for expenses / cash outflows where SV convention is positive)
YAHOO_TO_IFRS: list[tuple[tuple[str, ...], str, str, str, str]] = [
    # Income statement (duration)
    (("Total Revenue", "Operating Revenue"), "Revenue", "duration", "money", "as_is"),
    (("Cost Of Revenue", "Reconciled Cost Of Revenue"), "CostOfSales", "duration", "money", "abs"),
    (("Gross Profit",), "GrossProfit", "duration", "money", "as_is"),
    (("Research And Development",), "ResearchAndDevelopmentExpense", "duration", "money", "abs"),
    (("Selling General And Administration",), "SellingGeneralAndAdministrativeExpense", "duration", "money", "abs"),
    (("Operating Income", "EBIT"), "ProfitLossFromOperatingActivities", "duration", "money", "as_is"),
    (("Interest Expense", "Interest Expense Non Operating"), "FinanceCosts", "duration", "money", "abs"),
    (("Pretax Income",), "ProfitLossBeforeTax", "duration", "money", "as_is"),
    (("Tax Provision",), "IncomeTaxExpenseContinuingOperations", "duration", "money", "as_is"),
    (("Net Income", "Net Income Common Stockholders"), "ProfitLossAttributableToOwnersOfParent", "duration", "money", "as_is"),
    (("Minority Interests",), "ProfitLossAttributableToNoncontrollingInterests", "duration", "money", "as_is"),
    (("Net Income Including Noncontrolling Interests",), "ProfitLoss", "duration", "money", "as_is"),
    (("Basic EPS",), "BasicEarningsLossPerShare", "duration", "per_share", "as_is"),
    (("Diluted EPS",), "DilutedEarningsLossPerShare", "duration", "per_share", "as_is"),
    (("Basic Average Shares",), "WeightedAverageShares", "duration", "shares", "abs"),
    (("Diluted Average Shares",), "AdjustedWeightedAverageShares", "duration", "shares", "abs"),
    # Balance sheet (instant)
    (("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"), "CashAndCashEquivalents", "instant", "money", "as_is"),
    (("Other Short Term Investments",), "CurrentInvestments", "instant", "money", "as_is"),
    (("Accounts Receivable", "Receivables"), "TradeAndOtherCurrentReceivables", "instant", "money", "as_is"),
    (("Inventory",), "Inventories", "instant", "money", "as_is"),
    (("Current Assets",), "CurrentAssets", "instant", "money", "as_is"),
    (("Net PPE",), "PropertyPlantAndEquipment", "instant", "money", "as_is"),
    (("Goodwill",), "Goodwill", "instant", "money", "as_is"),
    (("Other Intangible Assets",), "IntangibleAssetsOtherThanGoodwill", "instant", "money", "as_is"),
    (("Total Assets",), "Assets", "instant", "money", "as_is"),
    (("Accounts Payable", "Payables And Accrued Expenses"), "TradeAndOtherCurrentPayables", "instant", "money", "as_is"),
    (("Current Liabilities",), "CurrentLiabilities", "instant", "money", "as_is"),
    (("Current Debt", "Current Debt And Capital Lease Obligation"), "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", "instant", "money", "as_is"),
    (("Long Term Debt", "Long Term Debt And Capital Lease Obligation"), "NoncurrentPortionOfNoncurrentBorrowings", "instant", "money", "as_is"),
    (("Total Liabilities Net Minority Interest",), "Liabilities", "instant", "money", "as_is"),
    (("Stockholders Equity", "Common Stock Equity"), "EquityAttributableToOwnersOfParent", "instant", "money", "as_is"),
    (("Minority Interest",), "NoncontrollingInterests", "instant", "money", "as_is"),
    (("Total Equity Gross Minority Interest",), "Equity", "instant", "money", "as_is"),
    (("Ordinary Shares Number", "Share Issued"), "NumberOfSharesOutstanding", "instant", "shares", "abs"),
    # Cash flow statement (duration)
    (("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"), "CashFlowsFromUsedInOperatingActivities", "duration", "money", "as_is"),
    (("Depreciation And Amortization", "Depreciation Amortization Depletion"), "DepreciationAndAmortisationExpense", "duration", "money", "abs"),
    (("Capital Expenditure", "Purchase Of PPE"), "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "duration", "money", "abs"),
    (("Investing Cash Flow", "Cash Flow From Continuing Investing Activities"), "CashFlowsFromUsedInInvestingActivities", "duration", "money", "as_is"),
    (("Cash Dividends Paid", "Common Stock Dividend Paid"), "DividendsPaidClassifiedAsFinancingActivities", "duration", "money", "abs"),
    (("Financing Cash Flow", "Cash Flow From Continuing Financing Activities"), "CashFlowsFromUsedInFinancingActivities", "duration", "money", "as_is"),
]


def _unit_str(unit_kind: str, currency: str) -> str:
    if unit_kind == "shares":
        return "shares"
    if unit_kind == "per_share":
        return f"{currency}/shares"
    return currency


def _fiscal_start(period_end: dt.date) -> dt.date:
    try:
        return period_end.replace(year=period_end.year - 1) + dt.timedelta(days=1)
    except ValueError:  # Feb 29 leap day
        return dt.date(period_end.year - 1, 3, 1)


def parse_yahoo_annual_facts(
    ticker: str,
    annual: Mapping[str, Mapping[str, float | int | Decimal | str]],
    currency: str = "HKD",
    fetched_date: Optional[dt.date] = None,
) -> tuple[list[FilingSummary], list[FiledFact], list[FiledConcept]]:
    """Convert Yahoo annual statements dict ``{YYYY-MM-DD: {row: num}}`` into ``ifrs-full`` FiledFacts."""
    code = normalize_hk_code(ticker) if is_hk_ticker(ticker) else re.sub(r"[^A-Z0-9]", "", ticker.upper())[:6]
    filed_on = fetched_date or dt.date.today()
    ccy = (currency or "HKD").upper()
    filings: list[FilingSummary] = []
    facts: list[FiledFact] = []
    concepts: dict[str, FiledConcept] = {}

    for day_str, row_map in sorted(annual.items()):
        try:
            period_end = dt.date.fromisoformat(str(day_str)[:10])
        except ValueError:
            continue
        accession = f"YH-{code}-{period_end.strftime('%Y%m%d')}"[:20]
        # Set Yahoo filed date to period_end + 90d (capped at fetched_date) so official HKEX-AR filed later wins when both exist
        yahoo_filed = min(filed_on, period_end + dt.timedelta(days=90))
        filings.append(FilingSummary(
            accession=accession,
            form="YAHOO-AR",
            filed=yahoo_filed,
            report_date=period_end,
            primary_document=f"https://finance.yahoo.com/quote/{ticker}/financials",
            description=f"Yahoo Finance annual statements ({period_end.isoformat()})",
        ))
        period_start = _fiscal_start(period_end)
        for names, concept, period_kind, unit_kind, sign_mode in YAHOO_TO_IFRS:
            raw_val = None
            matched_name = None
            for n in names:
                if n in row_map and row_map[n] is not None:
                    raw_val = row_map[n]
                    matched_name = n
                    break
            if raw_val is None:
                continue
            try:
                val = Decimal(str(raw_val))
            except (InvalidOperation, ValueError):
                continue
            if sign_mode == "abs":
                val = abs(val)
            concepts.setdefault(concept, FiledConcept("ifrs-full", concept, matched_name, f"Mapped from Yahoo Finance row: {matched_name}"))
            facts.append(FiledFact(
                taxonomy="ifrs-full",
                concept=concept,
                unit=_unit_str(unit_kind, ccy),
                value=val,
                period_start=period_start if period_kind == "duration" else None,
                period_end=period_end,
                fy=period_end.year,
                fp="FY",
                form="YAHOO-AR",
                accession=accession,
                filed=yahoo_filed,
                frame=f"CY{period_end.year}" if period_kind == "duration" else f"CY{period_end.year}Q4I",
            ))
    return filings, facts, list(concepts.values())


# ------------------------------------------------------------------ HKEX Annual Report PDF extraction

@dataclass(frozen=True)
class PdfFactMeta:
    concept: str
    period_end: dt.date
    page: int
    raw_label: str
    currency: str
    scale: int


@dataclass
class PdfExtractionResult:
    filings: list[FilingSummary] = field(default_factory=list)
    facts: list[FiledFact] = field(default_factory=list)
    concepts: list[FiledConcept] = field(default_factory=list)
    meta: list[PdfFactMeta] = field(default_factory=list)
    currency: str = "HKD"
    scale: int = 1
    fiscal_years: list[int] = field(default_factory=list)
    pages_scanned: int = 0


# Bilingual (English + Traditional/Simplified Chinese) IFRS/HKFRS line-item patterns
PDF_LINE_PATTERNS: list[tuple[re.Pattern, str, str, str, str]] = [
    # (regex, ifrs_concept, period_kind, unit_kind, sign_mode)
    (re.compile(r"^(?:total\s+)?(?:revenue|revenues|turnover|營業額|营业额|收入|收益|總收入|总收入)(?:\s*note.*)?$", re.I),
     "Revenue", "duration", "money", "as_is"),
    (re.compile(r"^(?:cost\s+of\s+(?:sales|revenues?|goods\s+sold)|銷售成本|销售成本|收入成本|營業成本|营业成本)$", re.I),
     "CostOfSales", "duration", "money", "abs"),
    (re.compile(r"^(?:gross\s+profit|毛利)$", re.I),
     "GrossProfit", "duration", "money", "as_is"),
    (re.compile(r"^(?:operating\s+(?:profit|income)|profit\s+from\s+operations|經營溢利|经营溢利|經營利潤|营业利润)$", re.I),
     "ProfitLossFromOperatingActivities", "duration", "money", "as_is"),
    (re.compile(r"^(?:finance\s+costs?|interest\s+expenses?|融資成本|融资成本|財務費用|财务费用)$", re.I),
     "FinanceCosts", "duration", "money", "abs"),
    (re.compile(r"^(?:profit\s+before\s+(?:income\s+)?tax(?:ation)?|除稅前溢利|除税前溢利|稅前利潤|税前利润)$", re.I),
     "ProfitLossBeforeTax", "duration", "money", "as_is"),
    (re.compile(r"^(?:income\s+tax(?:\s+expense)?|taxation|所得稅(?:開支|費用)?|所得税(?:费用)?|稅項|税项)$", re.I),
     "IncomeTaxExpenseContinuingOperations", "duration", "money", "abs"),
    (re.compile(r"^(?:profit\s+attributable\s+to\s+(?:equity\s+holders|owners|shareholders)(?:\s+of\s+the\s+company)?|本公司(?:權益持有人|擁有人|股東)應佔(?:溢利|利潤)|归属于母公司(?:所有者|股东)的净利润)$", re.I),
     "ProfitLossAttributableToOwnersOfParent", "duration", "money", "as_is"),
    (re.compile(r"^(?:profit\s+for\s+the\s+year|net\s+profit|本年(?:度)?溢利|年度利潤|净利润)$", re.I),
     "ProfitLoss", "duration", "money", "as_is"),
    (re.compile(r"^(?:basic\s+earnings\s+per\s+share|基本每股(?:盈利|收益))$", re.I),
     "BasicEarningsLossPerShare", "duration", "per_share", "as_is"),
    (re.compile(r"^(?:diluted\s+earnings\s+per\s+share|攤薄每股(?:盈利|收益)|稀释每股收益)$", re.I),
     "DilutedEarningsLossPerShare", "duration", "per_share", "as_is"),
    # Balance sheet
    (re.compile(r"^(?:cash\s+and\s+cash\s+equivalents|現金及現金等價物|现金及现金等价物)$", re.I),
     "CashAndCashEquivalents", "instant", "money", "as_is"),
    (re.compile(r"^(?:total\s+current\s+assets|流動資產總值|流動資產合計|流动资产合计)$", re.I),
     "CurrentAssets", "instant", "money", "as_is"),
    (re.compile(r"^(?:total\s+assets|資產總值|總資產|资产总计|总资产)$", re.I),
     "Assets", "instant", "money", "as_is"),
    (re.compile(r"^(?:total\s+current\s+liabilities|流動負債總值|流動負債合計|流动负债合计)$", re.I),
     "CurrentLiabilities", "instant", "money", "as_is"),
    (re.compile(r"^(?:total\s+liabilities|負債總值|總負債|负债总计|总负债)$", re.I),
     "Liabilities", "instant", "money", "as_is"),
    (re.compile(r"^(?:equity\s+attributable\s+to\s+(?:equity\s+holders|owners|shareholders)(?:\s+of\s+the\s+company)?|本公司(?:權益持有人|擁有人|股東)應佔權益|归属于母公司(?:所有者|股东)权益合计)$", re.I),
     "EquityAttributableToOwnersOfParent", "instant", "money", "as_is"),
    (re.compile(r"^(?:total\s+equity|權益總額|總權益|股東權益合計|所有者权益合计)$", re.I),
     "Equity", "instant", "money", "as_is"),
    # Cash flow
    (re.compile(r"^(?:net\s+cash\s+(?:flows?\s+)?(?:generated\s+from|from)\s+operating\s+activities|經營活動(?:所得|產生)現金(?:流量)?淨額|经营活动产生的现金流量净额)$", re.I),
     "CashFlowsFromUsedInOperatingActivities", "duration", "money", "as_is"),
    (re.compile(r"^(?:(?:purchase|additions)\s+of\s+property,\s*plant\s+and\s+equipment|capital\s+expenditure|購買物業、廠房及設備|购建固定资产)$", re.I),
     "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "duration", "money", "abs"),
    (re.compile(r"^(?:net\s+cash\s+(?:flows?\s+)?(?:used\s+in|from)\s+investing\s+activities|投資活動(?:所用|所得)現金(?:流量)?淨額|投资活动产生的现金流量净额)$", re.I),
     "CashFlowsFromUsedInInvestingActivities", "duration", "money", "as_is"),
    (re.compile(r"^(?:net\s+cash\s+(?:flows?\s+)?(?:used\s+in|from)\s+financing\s+activities|融資活動(?:所用|所得)現金(?:流量)?淨額|筹资活动产生的现金流量净额)$", re.I),
     "CashFlowsFromUsedInFinancingActivities", "duration", "money", "as_is"),
]

NUM_TOKEN_RE = re.compile(r"^\(?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?$")
YEAR_TOKEN_RE = re.compile(r"^(?:FY)?(20\d{2})$", re.I)


def detect_currency_and_scale(text: str, default_currency: str = "HKD") -> tuple[str, int]:
    """Detect reporting currency and unit multiplier (1 / 1,000 / 1,000,000) from statement header text."""
    head = text[:2000]
    ccy = default_currency.upper()
    if re.search(r"(?:\bRMB\b|\bCNY\b|人民幣|人民币)", head, re.I):
        ccy = "CNY"
    elif re.search(r"(?:US\$|\bUSD\b|美元)", head, re.I):
        ccy = "USD"
    elif re.search(r"(?:HK\$|\bHKD\b|港元|港幣|港币)", head, re.I):
        ccy = "HKD"

    scale = 1
    if re.search(r"(?:in\s+millions|million|百萬元|百万元|百萬|百万|\$'m\b|'m\b)", head, re.I):
        scale = 1_000_000
    elif re.search(r"(?:'000|thousand|千元|千港元|千人民幣|千人民币|千美元)", head, re.I):
        scale = 1_000
    return ccy, scale


def _detect_fiscal_month_day(text: str) -> tuple[int, int]:
    m = re.search(r"(?:ended|as\s+at)\s+31\s+March|三月三十一日|3月31日", text, re.I)
    if m:
        return (3, 31)
    m = re.search(r"(?:ended|as\s+at)\s+30\s+June|六月三十日|6月30日", text, re.I)
    if m:
        return (6, 30)
    m = re.search(r"(?:ended|as\s+at)\s+30\s+September|九月三十日|9月30日", text, re.I)
    if m:
        return (9, 30)
    return (12, 31)


def _parse_accounting_num(tok: str) -> Optional[Decimal]:
    s = tok.strip()
    if not NUM_TOKEN_RE.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")")
    cleaned = s.strip("()").replace(",", "")
    try:
        val = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -val if neg else val


def _extract_pdf_pages(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """Extract text page by page from PDF bytes (uses pypdf if installed, else built-in stream fallback)."""
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(io.BytesIO(pdf_bytes))
        return [(i + 1, (page.extract_text() or "")) for i, page in enumerate(reader.pages)]
    except Exception:
        import zlib

        pages: list[tuple[int, str]] = []
        page_idx = 0
        for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.S):
            chunk = m.group(1)
            try:
                decoded = zlib.decompress(chunk)
            except Exception:
                decoded = chunk
            if b"Tj" not in decoded and b"TJ" not in decoded:
                continue
            lines: list[str] = []
            for s in re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", decoded):
                lines.append(s.decode("latin-1", errors="ignore").replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\"))
            for arr in re.findall(rb"\[(.*?)\]\s*TJ", decoded, re.S):
                parts = [s.decode("latin-1", errors="ignore").replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
                         for s in re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)", arr)]
                if parts:
                    lines.append("".join(parts))
            txt = "\n".join(ln.strip() for ln in lines if ln.strip())
            if txt:
                page_idx += 1
                pages.append((page_idx, txt))
        return pages


def extract_hkex_pdf_facts(
    ticker: str,
    *,
    pdf_bytes: Optional[bytes] = None,
    pages: Optional[Sequence[tuple[int, str]]] = None,
    document_url: Optional[str] = None,
    filed_date: Optional[dt.date] = None,
    default_currency: str = "HKD",
) -> PdfExtractionResult:
    """Extract standardized ``ifrs-full`` facts from an HKEX Annual Report PDF (or pre-extracted pages)."""
    if pages is None:
        if pdf_bytes is None:
            raise ValueError("either pdf_bytes or pages must be supplied")
        pages = _extract_pdf_pages(pdf_bytes)

    code = normalize_hk_code(ticker) if is_hk_ticker(ticker) else re.sub(r"[^A-Z0-9]", "", ticker.upper())[:6]
    doc_hash = hashlib.sha256(pdf_bytes if pdf_bytes is not None else "\n".join(t for _, t in pages).encode()).hexdigest()[:6]
    filed_on = filed_date or dt.date.today()

    res = PdfExtractionResult(currency=default_currency.upper(), pages_scanned=len(pages))
    seen_facts: dict[tuple[str, dt.date], FiledFact] = {}
    concepts: dict[str, FiledConcept] = {}
    filings_by_year: dict[int, FilingSummary] = {}

    for page_no, raw_text in pages:
        if not raw_text.strip():
            continue
        ccy, scale = detect_currency_and_scale(raw_text, default_currency=res.currency)
        month, day = _detect_fiscal_month_day(raw_text)
        lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

        # Find column years on this page (e.g. "2024 2023" or "Note 2024 2023")
        col_years: list[int] = []
        for ln in lines[:25]:
            toks = re.split(r"\s+", ln)
            yrs = [int(m.group(1)) for tok in toks if (m := YEAR_TOKEN_RE.match(tok))]
            if len(yrs) >= 1 and len(yrs) <= 5 and all(2005 <= y <= 2035 for y in yrs):
                # Avoid picking up prose lines with a single year unless header-like
                if len(yrs) >= 2 or len(toks) <= 3:
                    col_years = yrs
                    break
        if not col_years:
            continue

        res.currency = ccy
        if scale > res.scale:
            res.scale = scale

        for ln in lines:
            toks = re.split(r"\s+", ln)
            if len(toks) < 2:
                continue
            # Pull trailing numeric columns matching col_years count
            nums: list[Decimal] = []
            idx = len(toks) - 1
            while idx >= 1 and len(nums) < len(col_years):
                n = _parse_accounting_num(toks[idx])
                if n is None:
                    break
                nums.append(n)
                idx -= 1
            if not nums:
                continue
            nums.reverse()
            # Drop optional note reference column (single small integer right before numbers)
            if idx >= 1 and re.fullmatch(r"\d{1,2}", toks[idx]) and len(nums) == len(col_years):
                idx -= 1
            label = " ".join(toks[: idx + 1]).strip(" :：.-—")
            if not label:
                continue

            matched = None
            for pat, concept, period_kind, unit_kind, sign_mode in PDF_LINE_PATTERNS:
                if pat.match(label):
                    matched = (concept, period_kind, unit_kind, sign_mode)
                    break
            if matched is None:
                continue
            concept, period_kind, unit_kind, sign_mode = matched
            concepts.setdefault(concept, FiledConcept("ifrs-full", concept, label, f"Extracted from HKEX annual report PDF (page {page_no})"))

            # Align extracted numbers with the rightmost / matching column years
            paired_years = col_years[: len(nums)]
            for yr, raw_num in zip(paired_years, nums):
                period_end = dt.date(yr, month, day)
                period_start = _fiscal_start(period_end) if period_kind == "duration" else None
                val = raw_num * Decimal(scale if unit_kind == "money" else 1)
                if sign_mode == "abs":
                    val = abs(val)
                accession = f"HK-{code}-{yr}-{doc_hash}"[:20]
                if yr not in filings_by_year:
                    filings_by_year[yr] = FilingSummary(
                        accession=accession,
                        form="HKEX-AR",
                        filed=max(filed_on, period_end + dt.timedelta(days=105)),
                        report_date=period_end,
                        primary_document=document_url or "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",
                        description=f"HKEX Annual Report PDF FY{yr} (page {page_no})",
                    )
                fact = FiledFact(
                    taxonomy="ifrs-full",
                    concept=concept,
                    unit=_unit_str(unit_kind, ccy),
                    value=val,
                    period_start=period_start,
                    period_end=period_end,
                    fy=yr,
                    fp="FY",
                    form="HKEX-AR",
                    accession=accession,
                    filed=filings_by_year[yr].filed or filed_on,
                    frame=f"CY{yr}" if period_kind == "duration" else f"CY{yr}Q4I",
                )
                key = (concept, period_end)
                if key not in seen_facts:
                    seen_facts[key] = fact
                    res.meta.append(PdfFactMeta(concept=concept, period_end=period_end, page=page_no, raw_label=label, currency=ccy, scale=scale))

    res.filings = [filings_by_year[y] for y in sorted(filings_by_year, reverse=True)]
    res.facts = list(seen_facts.values())
    res.concepts = list(concepts.values())
    res.fiscal_years = sorted(filings_by_year, reverse=True)
    return res


# ------------------------------------------------------------------ Cross-check: HKEX PDF vs Yahoo

@dataclass
class CrossCheckItem:
    concept: str
    period_end: str
    pdf_value: str
    yahoo_value: str
    rel_diff: float
    status: str       # "match" | "mismatch"


@dataclass
class CrossCheckReport:
    tolerance: float
    matched: list[CrossCheckItem] = field(default_factory=list)
    discrepancies: list[CrossCheckItem] = field(default_factory=list)
    pdf_only: int = 0
    yahoo_only: int = 0

    @property
    def compared(self) -> int:
        return len(self.matched) + len(self.discrepancies)

    @property
    def agreement_rate(self) -> Optional[float]:
        if self.compared == 0:
            return None
        return round(len(self.matched) / self.compared, 4)

    def to_dict(self) -> dict:
        return {
            "tolerance": self.tolerance,
            "compared": self.compared,
            "matched_count": len(self.matched),
            "discrepancy_count": len(self.discrepancies),
            "agreement_rate": self.agreement_rate,
            "pdf_only": self.pdf_only,
            "yahoo_only": self.yahoo_only,
            "discrepancies": [
                {"concept": d.concept, "period_end": d.period_end, "pdf_value": d.pdf_value,
                 "yahoo_value": d.yahoo_value, "rel_diff": round(d.rel_diff, 4)}
                for d in self.discrepancies
            ],
        }


def cross_check_hkex_vs_yahoo(
    pdf_facts: Sequence[FiledFact],
    yahoo_facts: Sequence[FiledFact],
    tolerance: float = 0.01,
) -> CrossCheckReport:
    """Compare HKEX Annual Report PDF facts against Yahoo Finance facts (default 1% tolerance, roadmap §3.6)."""
    pdf_map = {(f.concept, f.period_end): f for f in pdf_facts}
    yah_map = {(f.concept, f.period_end): f for f in yahoo_facts}
    report = CrossCheckReport(tolerance=tolerance)

    for key, pf in sorted(pdf_map.items()):
        yf = yah_map.get(key)
        if yf is None:
            report.pdf_only += 1
            continue
        denom = max(abs(pf.value), abs(yf.value))
        rel = float(abs(pf.value - yf.value) / denom) if denom != 0 else 0.0
        item = CrossCheckItem(
            concept=pf.concept,
            period_end=pf.period_end.isoformat(),
            pdf_value=format(pf.value, "f"),
            yahoo_value=format(yf.value, "f"),
            rel_diff=rel,
            status="match" if rel <= tolerance else "mismatch",
        )
        if item.status == "match":
            report.matched.append(item)
        else:
            report.discrepancies.append(item)

    report.yahoo_only = sum(1 for k in yah_map if k not in pdf_map)
    return report


class HkexFreeProvider:
    """Implements :class:`FinancialDataProvider` for Hong Kong equities using Yahoo + HKEX PDF (D11)."""
    name = "hkex_free"
    markets = ("HKEX",)

    def supports(self, ticker: str) -> bool:
        return is_hk_ticker(ticker)

    def build_result(
        self,
        ticker: str,
        *,
        yahoo_annual: Optional[Mapping[str, Mapping[str, float | int | Decimal | str]]] = None,
        pdf_bytes: Optional[bytes] = None,
        pdf_pages: Optional[Sequence[tuple[int, str]]] = None,
        document_url: Optional[str] = None,
        entity_name: Optional[str] = None,
        currency: str = "HKD",
        sic: Optional[str] = None,
        tolerance: float = 0.01,
    ) -> ProviderResult:
        res = ProviderResult(
            provider=self.name,
            ticker=ticker.upper(),
            entity_id=hk_cik(ticker),
            entity_name=entity_name,
            currency=currency.upper(),
            sic=sic,
        )
        y_facts: list[FiledFact] = []
        if yahoo_annual:
            yf, y_facts, yc = parse_yahoo_annual_facts(ticker, yahoo_annual, currency=currency)
            res.filings.extend(yf)
            res.facts.extend(y_facts)
            res.concepts.extend(yc)
        p_facts: list[FiledFact] = []
        if pdf_bytes is not None or pdf_pages is not None:
            pdf_res = extract_hkex_pdf_facts(
                ticker, pdf_bytes=pdf_bytes, pages=pdf_pages, document_url=document_url, default_currency=currency
            )
            res.filings.extend(pdf_res.filings)
            res.facts.extend(pdf_res.facts)
            res.concepts.extend(pdf_res.concepts)
            p_facts = pdf_res.facts
            res.currency = pdf_res.currency
        if p_facts and y_facts:
            res.cross_check = cross_check_hkex_vs_yahoo(p_facts, y_facts, tolerance=tolerance).to_dict()
        return res
