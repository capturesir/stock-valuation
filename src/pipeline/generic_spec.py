"""Build a model spec for any ticker from fetched statements — no hand-written spec.

The hand-verified specs in ``name_specs.py`` stay authoritative for NVDA and
1830.HK. This module exists so a new ticker can be valued straight away:

* every number comes from the fetched Yahoo statements, the consensus estimates
  or the Damodaran files — nothing is typed in by hand and nothing is taken
  from a model's memory;
* the AI step may propose the growth / margin path, but its output is validated
  against the data-driven rules and rejected if it drifts too far (so the
  engine never receives invented numbers);
* anything missing is recorded in ``data_gaps`` and surfaced in the report, so a
  generic spec is always visibly lower-confidence than a hand-verified one.

Method A (Rf / ERP / CRP / industry beta) is unchanged: the industry is matched
from Yahoo's industry string against the bundled ``data/damodaran/betaGlobal.xls``;
only when no industry matches do we derive βU from the regression beta and say so.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Optional

from ..engine import method_a as MA
from ..engine.fcff_engine import D
from ..fetch import yahoo
from . import rules
from .gaps import Gap

# Yahoo "industry" (lower-case) -> Damodaran industry name in betaGlobal.xls.
# Only exact-ish matches are listed; anything else falls back to a derivation.
INDUSTRY_MAP = {
    "oil & gas integrated": "Oil/Gas (Production and Exploration)",
    "oil & gas e&p": "Oil/Gas (Production and Exploration)",
    "oil & gas midstream": "Oil/Gas (Transportation)",
    "semiconductors": "Semiconductor",
    "software - infrastructure": "Software (System & Application)",
    "software - application": "Software (System & Application)",
    "consumer electronics": "Electronics (General)",
    "electronic components": "Electronics (General)",
    "telecom services": "Telecom (Wireless)",
    "drug manufacturers - general": "Drugs (Pharmaceutical)",
    "drug manufacturers - specialty & generic": "Drugs (Pharmaceutical)",
    "banks - diversified": "Bank (Money Center)",
    "insurance - property & casualty": "Insurance (Prop. & Casualty)",
    "utilities - regulated electric": "Power",
    "utilities - independent power producers": "Power",
    "retail - cyclical": "Retail (General)",
    "internet retail": "Retail (General)",
    "retail - defensive": "Retail (Grocery and Food)",
    "restaurants": "Restaurant/Dining",
    "medical devices": "Healthcare Products",
    "medical care facilities": "Hospitals/Healthcare Facilities",
    "airlines": "Air Transport",
    "auto manufacturers": "Auto & Truck",
    "auto parts": "Auto Parts",
    "steel": "Steel",
    "gold": "Metals & Mining",
    "copper": "Metals & Mining",
    "building materials": "Building Materials",
    "packaged foods": "Food Processing",
    "beverages - non-alcoholic": "Beverage (Soft)",
    "household & personal products": "Household Products",
    "internet content & information": "Information Services",
    "advertising agencies": "Advertising",
    "entertainment": "Entertainment",
    "gambling": "Gaming",
    "railroads": "Railroad",
    "marine shipping": "Shipbuilding & Marine",
    "real estate services": "Real Estate Services",
}

MARKET_DEFAULTS = {
    "HK": {"market": "香港", "currency": "HKD", "terminal_g": 0.025},
    "US": {"market": "美國", "currency": "USD", "terminal_g": 0.03},
    "CN": {"market": "中國內地", "currency": "CNY", "terminal_g": 0.025},
    "JP": {"market": "日本", "currency": "JPY", "terminal_g": 0.02},
    "KR": {"market": "韓國", "currency": "KRW", "terminal_g": 0.025},
    "TW": {"market": "台灣", "currency": "TWD", "terminal_g": 0.025},
}

# Terminal growth ceiling by MODEL currency (also capped at that currency's Rf).
TERMINAL_G_BY_CCY = {"USD": 0.03, "HKD": 0.025, "CNY": 0.025, "JPY": 0.015, "KRW": 0.02, "TWD": 0.02, "SGD": 0.02}

# Statutory corporate income tax incl. typical local taxes, by Yahoo "country". Near-term NOPAT uses
# the effective rate, converging to this marginal rate by the last forecast year (Damodaran); the
# terminal year, beta relevering and the interest tax shield use the marginal rate. Review yearly.
MARGINAL_TAX = {"United States": 0.25, "China": 0.25, "Hong Kong": 0.165, "Japan": 0.3062, "South Korea": 0.264,
                "Taiwan": 0.20, "Singapore": 0.17, "United Kingdom": 0.25, "Germany": 0.30, "France": 0.258,
                "Netherlands": 0.258, "Canada": 0.265, "Australia": 0.30, "India": 0.252, "Switzerland": 0.196,
                "Ireland": 0.125, "Israel": 0.23, "Macau": 0.12}
CCY_TAX_COUNTRY = {"CNY": "China", "JPY": "Japan", "KRW": "South Korea", "TWD": "Taiwan", "HKD": "Hong Kong",
                   "SGD": "Singapore"}
MARGINAL_TAX_SOURCE = "各地法定企業所得稅率（含一般地方稅；KPMG Corporate tax rates table、Damodaran countrytaxrates.xls，須每年覆核）"
DEFAULT_MARGINAL_TAX = 0.25

# Industries whose long-term provisions are mostly asset-retirement / restoration obligations
# (debt-like: the cash outflow is not in the FCFF forecast). Elsewhere they may be operating
# (warranties), so they are reported, not deducted.
ARO_INDUSTRIES = ("Oil/Gas", "Metals & Mining", "Coal", "Power", "Utility", "Precious Metals", "Green & Renewable")

FINANCIAL_INDUSTRIES = ("banks", "insurance", "capital markets", "asset management", "credit services",
                        "mortgage finance", "financial conglomerates", "financial data")

# RONIC in the terminal period: excess returns fade. House rule (McKinsey "Valuation", ch. on
# continuing value; Damodaran stable growth): RONIC = WACC + half of the historical excess over
# WACC, the excess capped at 10pp; no excess -> RONIC = WACC (growth then creates no value).
RONIC_EXCESS_SHARE = 0.5
RONIC_EXCESS_CAP = 0.10

_BETA_TABLE: Optional[dict] = None


class GenericSpecError(RuntimeError):
    """Raised when the fetched data is not enough to build a defensible spec."""


# ---------------------------------------------------------------- Damodaran


def damodaran_betas() -> dict:
    """{industry: (avg_2020_24, point_2026)} from the bundled betaGlobal.xls."""
    global _BETA_TABLE
    if _BETA_TABLE is not None:
        return _BETA_TABLE
    table: dict = {}
    from pathlib import Path

    path = Path(MA.__file__).resolve().parents[2] / "data" / "damodaran" / "betaGlobal.xls"
    try:
        import xlrd  # optional dependency, listed in requirements

        sh = xlrd.open_workbook(str(path)).sheet_by_name("Industry Averages")
        header = None
        for r in range(sh.nrows):
            if str(sh.cell_value(r, 0)).strip().lower().startswith("industry name"):
                header = r
                break
        if header is not None:
            for r in range(header + 1, sh.nrows):
                name = str(sh.cell_value(r, 0)).strip()
                if not name:
                    continue
                point = sh.cell_value(r, 7)      # Unlevered beta corrected for cash
                avg = sh.cell_value(r, 15)       # Average (2020-24)
                if isinstance(point, float) and isinstance(avg, float):
                    table[name] = (avg, point)
    except Exception:
        table = {}
    if table:
        _BETA_TABLE = table
    else:  # fall back to the hand-transcribed subset in method_a
        _BETA_TABLE = {k: (v, MA.BETA_U_POINT.get(k, v)) for k, v in MA.BETA_U_AVG.items()}
    return _BETA_TABLE


def _register_industry(name: str) -> tuple[float, float]:
    table = damodaran_betas()
    avg, point = table[name]
    if name not in MA.BETA_U_AVG:
        MA.register_industry(
            name, avg, point,
            f"Damodaran betaGlobal.xls, sheet Industry Averages, 列 {name}；"
            f"Unlevered beta corrected for cash＝{point}，Average (2020-24)＝{avg}",
        )
    return avg, point


def _match_industry(yahoo_industry: str, table: dict) -> Optional[str]:
    text = (yahoo_industry or "").strip().lower()
    if not text:
        return None
    if text in INDUSTRY_MAP and INDUSTRY_MAP[text] in table:
        return INDUSTRY_MAP[text]
    # exact Damodaran name (case-insensitive)
    for name in table:
        if name.lower() == text:
            return name
    # token overlap, conservative: need a unique best hit with >= 2 shared tokens
    tokens = {t for t in text.replace("&", " ").replace("/", " ").replace("-", " ").split() if len(t) > 2}
    best, best_score = None, 0
    ties = 0
    for name in table:
        nt = {t for t in name.lower().replace("&", " ").replace("/", " ").replace("-", " ").split() if len(t) > 2}
        if len(tokens & nt) >= 2:
            score = len(tokens & nt)
            if score > best_score:
                best, best_score, ties = name, score, 1
            elif score == best_score:
                ties += 1
    return best if best and ties == 1 else None


def resolve_industry(ticker: str, info: dict, tax: float, d_over_e: float, gaps: list) -> str:
    """Register (ticker -> industry) so method_a.beta() works; return the label used."""
    if ticker in MA.IND:
        industry = MA.IND[ticker]
        if industry in damodaran_betas() and industry not in MA.BETA_U_AVG:
            _register_industry(industry)
        return industry

    table = damodaran_betas()
    industry = _match_industry(info.get("industry") or "", table) or _match_industry(info.get("sector") or "", table)
    if industry:
        _register_industry(industry)
        MA.register_ticker(ticker, industry)
        gaps.append(Gap("industry_mapped", yahoo=info.get('industry') or info.get('sector'), industry=industry))
        return industry

    # No Damodaran industry matched: derive the unlevered beta from the regression beta.
    reg = info.get("beta")
    if not reg or d_over_e <= 0:
        raise GenericSpecError(
            f"{ticker} 無法對應 Damodaran 行業（Yahoo 行業：{info.get('industry') or '無'}），"
            "亦無可用迴歸 Beta，無法按方法 A 計算折現率。"
        )
    bu = float(reg) / (1 + (1 - tax) * d_over_e)
    # Guard the derivation: an absurd βU means the inputs were inconsistent
    # (units, missing debt, extreme D/E) and would distort WACC silently.
    if not (0.05 <= bu <= 3.0):
        gaps.append(Gap("beta_unlevered_unreasonable", bu=bu, reg=float(reg)))
        bu = float(reg)
    label = f"未分類（{info.get('industry') or info.get('sector') or ticker}）"

    MA.register_industry(
        label, bu, bu,
        f"方法 A 例外：Yahoo 迴歸 Beta {reg} 按 D/E {d_over_e:.3f}、稅率 {tax:.1%} 還原為 βU {bu:.4f}；"
        "未能對應 Damodaran 行業，屬估算值。",
    )
    MA.register_ticker(ticker, label)
    gaps.append(Gap("beta_derived", bu=bu))
    return label


# ------------------------------------------------------------------ helpers


def _latest(block: dict, skip: int = 0) -> Optional[str]:
    if not block or "error" in block:
        return None
    keys = sorted(block.keys(), reverse=True)
    return keys[skip] if len(keys) > skip else None


def _val(block: dict, date: Optional[str], *names) -> Optional[float]:
    if not block or not date:
        return None
    row = block.get(date) or {}
    for n in names:
        v = row.get(n)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _first(block: dict, date: Optional[str], names) -> tuple[Optional[float], Optional[str]]:
    if isinstance(names, str):  # a bare string would be iterated character by character
        names = [names]
    for n in names:
        v = _val(block, date, n)
        if v is not None:
            return v, n
    return None, None


# One definition per model input, shared with pipeline/adjustments.py (what the user reviews is what
# the model uses). Operating income, not Yahoo "EBIT": Yahoo's EBIT is pretax income + interest
# expense, so it carries non-operating gains, impairments and other income.
EBIT_ROWS = ["Operating Income", "Total Operating Income As Reported", "EBIT"]
DA_CF_ROWS = ["Depreciation And Amortization", "Depreciation Amortization Depletion"]


def _op_income(is_a: dict, date: str) -> tuple[Optional[float], Optional[str]]:
    return _first(is_a, date, EBIT_ROWS)


def _da(is_a: dict, cf_a: dict, date: str) -> Optional[float]:
    v, _ = _first(cf_a, date, DA_CF_ROWS)
    if v is None:
        v, _ = _first(is_a, date, ["Reconciled Depreciation"])
    if v is None:
        v, _ = _first(cf_a, date, ["Depreciation"])
    return abs(v) if v is not None else None


def is_financial(info: dict) -> bool:
    ind = (info.get("industry") or "").lower()
    return (info.get("sector") or "").lower() == "financial services" or ind.startswith(FINANCIAL_INDUSTRIES)


def ronic_rule(roic: Optional[float], wacc: float) -> tuple[float, str]:
    if roic is None or roic <= wacc:
        why = ("歷史營運 ROIC 不可用" if roic is None else f"歷史營運 ROIC {roic:.1%} 不高於 WACC {wacc:.2%}")
        return wacc, f"終值 RONIC＝WACC {wacc:.2%}（{why}；永續期新投資不創造超額回報）"
    excess = min(roic, wacc + RONIC_EXCESS_CAP) - wacc
    r = wacc + RONIC_EXCESS_SHARE * excess
    return r, (f"終值 RONIC＝WACC {wacc:.2%}＋{RONIC_EXCESS_SHARE:.0%}×超額回報 {excess:.1%}"
               f"（歷史營運 ROIC {roic:.1%}，超額部份上限 {RONIC_EXCESS_CAP:.0%}）＝{r:.2%}；超額回報在永續期減半，反映競爭侵蝕")


def _fx_rate(model_ccy: str, market_ccy: str) -> float:
    """model -> market multiplier (e.g. CNY -> HKD)."""
    model_ccy, market_ccy = (model_ccy or "").upper(), (market_ccy or "").upper()
    if not model_ccy or not market_ccy or model_ccy == market_ccy:
        return 1.0
    pair = f"{model_ccy}{market_ccy}=X"
    ch = yahoo.chart_quote(pair)
    if not ch.get("ok") or not ch.get("last"):
        raise GenericSpecError(f"無法取得 {model_ccy}/{market_ccy} 匯率（Yahoo {pair}）：{ch.get('error')}")
    return float(ch["last"]["close"])


def market_code(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(".HK"):
        return "HK"
    if t.endswith(".T"):
        return "JP"
    if t.endswith(".KS") or t.endswith(".KQ"):
        return "KR"
    if t.endswith(".TW") or t.endswith(".TWO"):
        return "TW"
    if t.endswith(".SS") or t.endswith(".SZ"):
        return "CN"
    return "US"


# ------------------------------------------------------------------- builds


def build_generic_spec(snap: dict, fund: dict, cfg: Optional[dict] = None,
                       use_llm: bool = True, client=None) -> dict:
    """Return a spec dict for ``build_and_run``; raise GenericSpecError if too thin."""
    cfg = cfg or {}
    info = fund.get("info") or {}
    ticker = snap["ticker"]
    code = market_code(ticker)
    mk = MARKET_DEFAULTS[code]
    gaps: list = list(snap.get("data_gaps") or [])
    from_sec = (snap.get("statement_source") or {}).get("source") in ("sec_standard", "hkex_standard")
    gaps.append(Gap("generic_spec_sec" if from_sec else "generic_spec_yahoo"))

    is_a, bs_a, cf_a = fund.get("is_a") or {}, fund.get("bs_a") or {}, fund.get("cf_a") or {}
    dates = sorted(is_a.keys(), reverse=True) if isinstance(is_a, dict) else []
    if len(dates) < 2:
        raise GenericSpecError(f"{ticker} Yahoo 年度財報不足（只取得 {len(dates)} 期），無法建立模型。")
    d0 = dates[0]
    bs0 = _latest(bs_a)

    rev, rev_name = _first(is_a, d0, ["Total Revenue", "Operating Revenue"])
    if not rev or rev <= 0:
        raise GenericSpecError(f"{ticker} 取不到年度營收，無法建立模型。")
    ebit, ebit_name = _op_income(is_a, d0)
    if ebit_name == "EBIT":
        gaps.append(Gap("ebit_from_yahoo"))
    if ebit is None:
        raise GenericSpecError(f"{ticker} 取不到年度 EBIT／營業利潤，無法建立模型。")

    m0 = ebit / rev
    if ebit <= 0:
        raise GenericSpecError(
            f"{ticker} 最近年度 EBIT 為負（{(ebit / 1e6):,.1f} 百萬，約 {m0:.1%} 營收），"
            "本引擎的 FCFF 五情境模型假設正 NOPAT，對虧損公司會給出沒有意義（甚至負值）的理論價。"
            "此類公司需先建立虧損轉盈的專用模型（例如按分部或按現金消耗估算），不可用通用規格硬算。"
        )
    da = _da(is_a, cf_a, d0)
    capex_neg, _ = _first(cf_a, d0, ["Capital Expenditure", "Capital Expenditure Reported", "Purchase Of PPE"])
    capex = abs(capex_neg) if capex_neg is not None else None
    ppe_dep, _ = _first(cf_a, d0, ["Depreciation"])
    tax = _val(is_a, d0, "Tax Rate For Calcs")
    if tax is None:
        pretax, provision = _val(is_a, d0, "Pretax Income"), _val(is_a, d0, "Tax Provision")
        tax = (provision / pretax) if pretax and provision is not None and pretax else None
    interest, _ = _first(is_a, d0, ["Interest Expense", "Interest Expense Non Operating"])
    # Diluted shares (standard): period-end basic shares + the dilution the company itself reports
    # (diluted − basic weighted average, same fiscal year), so options / RSUs / convertibles count.
    basic = _val(bs_a, bs0, "Ordinary Shares Number", "Share Issued")
    basic_src = f"{bs0} 期末已發行股數（Yahoo balance sheet）"
    if not basic:
        basic = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding")
        basic_src = "Yahoo 目前已發行股數（資產負債表無股數）"
        gaps.append(Gap("shares_from_info"))
    if not basic:
        raise GenericSpecError(f"{ticker} 取不到股數，無法換算每股理論價。")
    dil_avg, bas_avg = _val(is_a, d0, "Diluted Average Shares"), _val(is_a, d0, "Basic Average Shares")
    dilution = max(0.0, dil_avg - bas_avg) if dil_avg and bas_avg else 0.0
    shares = float(basic) + dilution
    shares_m = shares / 1e6
    shares_basis = (f"市場數據：{basic_src} {float(basic) / 1e6:,.3f}m ＋ 攤薄 {dilution / 1e6:,.3f}m"
                    f"（{d0} 攤薄加權平均 − 基本加權平均）＝ {shares_m:,.3f}m 攤薄股數")
    if not (dil_avg and bas_avg):
        gaps.append(Gap("no_dilution"))

    cash, _ = _first(bs_a, bs0, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])
    if cash is None:
        c1, c2 = _val(bs_a, bs0, "Cash And Cash Equivalents"), _val(bs_a, bs0, "Other Short Term Investments")
        cash = (c1 or 0.0) + (c2 or 0.0) if (c1 or c2) else None
    debt, _ = _first(bs_a, bs0, ["Total Debt"])
    if debt is None:
        parts = [_val(bs_a, bs0, "Long Term Debt"), _val(bs_a, bs0, "Current Debt"),
                 _val(bs_a, bs0, "Capital Lease Obligations")]
        debt = sum(p for p in parts if p) or None
    # _first() takes a list of row names. Passing a bare string iterated over its
    # characters, so neither row was ever found and the bridge silently skipped
    # 非控股權益 and 遞延稅項負債 (CNOOC: 16.0bn CNY never deducted).
    dtl, _ = _first(bs_a, bs0, ["Non Current Deferred Taxes Liabilities"])
    nci, _ = _first(bs_a, bs0, ["Minority Interest"])

    for gap_code, v in (("missing_da", da), ("missing_capex", capex), ("missing_rf", snap.get("rf", {}).get("value"))):
        if v is None:
            gaps.append(Gap(gap_code))

    # Tax follows where profits are earned, approximated like the CRP by the reporting currency
    # (CNOOC is registered in Hong Kong but taxed as a mainland resident enterprise at 25%).
    rep_ccy = (info.get("financialCurrency") or mk["currency"] or "").upper()
    country = CCY_TAX_COUNTRY.get(rep_ccy) or info.get("country") or ""
    marginal = MARGINAL_TAX.get(country)
    if marginal is None:
        marginal = DEFAULT_MARGINAL_TAX
        gaps.append(Gap("tax_country_unknown", country=country or "—", marginal=marginal))
    eff_raw = tax
    tax = marginal if tax is None or not (0.0 <= float(tax) <= 0.5) else float(tax)
    if eff_raw is None or not (0.0 <= float(eff_raw) <= 0.5):
        gaps.append(Gap("effective_tax_invalid", eff=str(eff_raw), marginal=marginal))
    da_pct = (da / rev) if da else 0.05
    cpx_pct = (capex / rev) if capex else max(da_pct, 0.02)

    # working capital: AR + inventory - AP - deferred revenue
    ar = _val(bs_a, bs0, "Accounts Receivable") or 0.0
    inv = _val(bs_a, bs0, "Inventory") or 0.0
    ap = _val(bs_a, bs0, "Accounts Payable") or 0.0
    defrev = _val(bs_a, bs0, "Current Deferred Revenue") or 0.0
    nwc_pct_raw = (ar + inv - ap - defrev) / rev
    # Negative working capital (deferred revenue / payables > receivables + inventory) should not
    # manufacture perpetual cash inflows when projecting 10-year revenue growth.
    if nwc_pct_raw < 0.0:
        nwc_pct = 0.0
        gaps.append(Gap("negative_nwc_capped", raw=nwc_pct_raw))
    else:
        nwc_pct = nwc_pct_raw

    # history for percentages and growth
    hist_rev = [(dt, _val(is_a, dt, "Total Revenue", "Operating Revenue")) for dt in dates[:4]]
    hist_rev = [(d, v) for d, v in hist_rev if v]
    rev_cagr = None
    if len(hist_rev) >= 2:
        n_years = len(hist_rev) - 1
        rev_cagr = (hist_rev[0][1] / hist_rev[-1][1]) ** (1 / n_years) - 1
    margin_hist = [(dt_, _op_income(is_a, dt_)[0]) for dt_, _ in hist_rev]
    margins_hist = [v / r for (_, r), (_, v) in zip(hist_rev, margin_hist) if v is not None and r]
    m_avg = sum(margins_hist) / len(margins_hist) if margins_hist else m0
    da_hist = [(dt_, _da(is_a, cf_a, dt_)) for dt_, _ in hist_rev]
    da_pcts_hist = [v / r for (_, r), (_, v) in zip(hist_rev, da_hist) if v]
    cpx_hist = [abs(_val(cf_a, dt_, "Capital Expenditure") or 0) / r for dt_, r in hist_rev
                if _val(cf_a, dt_, "Capital Expenditure")]
    da_pct = sum(da_pcts_hist) / len(da_pcts_hist) if da_pcts_hist else da_pct
    cpx_pct = sum(cpx_hist) / len(cpx_hist) if cpx_hist else cpx_pct

    # ---- FX first: statements are in the financial currency, the price is not
    model_ccy = (info.get("financialCurrency") or mk["currency"]).upper()
    mkt_ccy = (info.get("currency") or mk["currency"]).upper()
    fx_model_to_mkt = _fx_rate(model_ccy, mkt_ccy)
    fx_mkt_to_model = 1.0 / fx_model_to_mkt if fx_model_to_mkt else 1.0

    # ---- discount-rate inputs
    rf = D(snap["rf"]["value"], snap["rf"]["basis"], snap["rf"]["source"], snap["rf"]["url"], snap["rf"]["data_date"])
    # Country risk follows where the business is, approximated by the reporting currency (a HK-listed
    # company reporting in CNY operates mainly in mainland China), not by the listing venue.
    from ..fetch import rf as _rfmod
    crp_country = _rfmod.COUNTRY.get(model_ccy) if _rfmod.COUNTRY.get(model_ccy) in MA.CRP else code
    if crp_country not in MA.CRP:
        gaps.append(Gap("crp_missing", market=crp_country))
        crp_country = "US"
    crp = MA.crp(crp_country, f"（通用規格：按報表貨幣 {model_ccy} 推定業務所在地；跨國業務應按收入地域加權）"
                 if crp_country != "US" else "")
    if crp_country != code and code in MA.CRP:
        gaps.append(Gap("crp_by_currency", currency=model_ccy, country=crp_country, country_name=MA.CRP[crp_country][2],
                        listing=code, listing_name=MA.CRP[code][2]))
    e_mkt_model = snap["price"] * fx_mkt_to_model * shares_m

    kd = (interest / debt) if interest and debt and debt > 0 else None
    if kd is None or not (0 < kd < 0.5):
        kd = snap["rf"]["value"] + MA.DEFAULT_SPREAD.get(code, 0.005)
        gaps.append(Gap("kd_fallback"))
    # keep units consistent: debt is in currency units, E is in millions.
    # (Using the raw debt here inflated D/E by 1e6, collapsed βU to ~0 and sent
    # Alibaba's WACC to 3.5% — caught by the fresh-ticker acceptance test.)
    d_over_e = ((debt or 0.0) / 1e6) / e_mkt_model if e_mkt_model else 0.0


    industry = resolve_industry(ticker, info, marginal, d_over_e, gaps)

    # ---- non-operating assets and debt equivalents (EV -> equity bridge)
    lt_eq, _ = _first(bs_a, bs0, ["Long Term Equity Investment"])
    if lt_eq is None:
        a, j = _val(bs_a, bs0, "Investmentsin Associatesat Cost"), _val(bs_a, bs0, "Investmentsin Joint Venturesat Cost")
        lt_eq = (a or 0.0) + (j or 0.0) if (a or j) else None
    fin_assets, _ = _first(bs_a, bs0, ["Investmentin Financial Assets"])
    pref, _ = _first(bs_a, bs0, ["Preferred Stock Equity", "Preferred Stock"])
    pension, pension_row = _first(bs_a, bs0, ["Non Current Pension And Other Postretirement Benefit Plans",
                                              "Pensionand Other Post Retirement Benefit Plans", "Employee Benefits"])
    provisions, _ = _first(bs_a, bs0, ["Long Term Provisions"])
    aro_industry = any(k.lower() in industry.lower() for k in ARO_INDUSTRIES)

    # ---- operating ROIC -> terminal RONIC (needs WACC, computed exactly as the engine will)
    equity_total = _val(bs_a, bs0, "Total Equity Gross Minority Interest", "Stockholders Equity")
    ic = None
    if equity_total is not None:
        ic = equity_total + (debt or 0.0) - (cash or 0.0) - (lt_eq or 0.0) - (fin_assets or 0.0)
    roic = (ebit * (1 - tax)) / ic if ic and ic > 0 else None
    _beta_ref, _beta_ado, _ = MA.beta(ticker, (debt or 0.0) / 1e6, e_mkt_model, marginal, info.get("beta"), "", "")
    from ..engine.fcff_engine import discount_block as _disc
    _d, _ = _disc(rf=rf, erp=MA.erp(snap.get("valuation_date")), crp=crp, beta_ref=_beta_ref, beta_adopted=_beta_ado,
                  kd_pre=D(kd, ""), tax=D(marginal, ""), E=D(e_mkt_model, ""), Dv=D((debt or 0.0) / 1e6, ""))
    wacc_est = _d["wacc"]["value"]
    ronic, ronic_note = ronic_rule(roic, wacc_est)
    if is_financial(info):
        method_warning = Gap("financial_method")
        gaps.insert(0, method_warning)
    else:
        method_warning = None

    # ---- forecast paths (rules), then optional AI proposal
    n = 10
    terminal_g = min(TERMINAL_G_BY_CCY.get(model_ccy, mk["terminal_g"]), float(snap["rf"]["value"]))
    rev_est = fund.get("rev_est") or {}
    g0 = g1 = None
    cons_note = "Yahoo 無營收一致預期，採用歷史增長。"
    try:
        y0 = (rev_est.get("0y") or {})
        y1 = (rev_est.get("+1y") or {})
        prev_rev0 = y0.get("yearAgoRevenue")
        if y0.get("avg"):
            avg0 = float(y0["avg"])
            g_from_stmt = avg0 / rev - 1.0        # consensus vs last reported year
            g_from_yahoo = None
            if prev_rev0:
                try:
                    g_from_yahoo = avg0 / float(prev_rev0) - 1.0
                except (TypeError, ValueError, ZeroDivisionError):
                    g_from_yahoo = None
            # Yahoo's yearAgoRevenue is sometimes a different scope or period
            # (Toyota 7203.T: 18.3tn vs 50.7tn actually reported), which turned
            # into a +197% first year. Trust it only when it is roughly the same
            # size as the reported revenue.
            same_scale = prev_rev0 and abs(float(prev_rev0) / rev - 1.0) <= 0.4
            if g_from_yahoo is not None and same_scale:
                g0 = g_from_yahoo
            else:
                g0 = g_from_stmt
                if g_from_yahoo is not None:
                    gaps.append(Gap("consensus_scale_mismatch", prev_m=float(prev_rev0) / 1e6, rev_m=rev / 1e6, g0=g0))
        if y1.get("avg") and y0.get("avg"):
            g1 = float(y1["avg"]) / float(y0["avg"]) - 1
        if g0 is not None:
            cons_note = (f"Yahoo 分析師營收共識：本期 {g0:.2%}"
                         + (f"、下期 {g1:.2%}" if g1 is not None else ""))
    except (TypeError, ValueError):
        pass

    if g0 is None:
        g0 = rev_cagr if rev_cagr is not None else terminal_g
        g0 = max(min(g0, 0.6), -0.3)
        gaps.append(Gap("growth_from_cagr", years=len(hist_rev) - 1, g0=g0))
    # consensus series can jump wildly (e.g. a low base year); clamp before use
    def _clamp_g(x, label):
        lo, hi = -0.5, 1.5
        if x is not None and not (lo <= x <= hi):
            gaps.append(Gap("consensus_clamped", label=label, x=x, lo=lo, hi=hi))
            return max(min(x, hi), lo)
        return x

    g0 = _clamp_g(g0, "首年")
    g1 = _clamp_g(g1, "次年")
    starts = [float(g0)] + ([float(g1)] if g1 is not None else [])
    growths = rules.fade_growth_convex(starts, terminal_g, n)

    # Cyclical / volatile operating margin normalization:
    # Avoid extrapolating a cyclical peak EBIT margin over 10 years by reverting toward the
    # multi-year median margin when the industry is cyclical or historical spread >= 8pp.
    cyclical_kws = (
        "oil", "gas", "energy", "coal", "steel", "mining", "metal", "semiconductor",
        "shipping", "marine", "airline", "chemical", "auto", "commodity", "paper", "building",
    )
    ind_l = f"{industry} {info.get('sector') or ''} {info.get('industry') or ''}".lower()
    m_med = statistics.median(margins_hist) if margins_hist else m0
    m_spread = (max(margins_hist) - min(margins_hist)) if len(margins_hist) >= 3 else 0.0
    is_cyclical = any(k in ind_l for k in cyclical_kws) or m_spread >= 0.08
    margin_target = m_avg
    if is_cyclical and len(margins_hist) >= 3 and m0 > m_med + 0.03:
        margin_target = m_med + 0.25 * (m0 - m_med)
        margins = [m0] + rules.fade_levels(m0, margin_target, n)[1:]
        gaps.append(Gap("cyclical_margin_normalized", m0=m0, median=m_med, target=margin_target))
    else:
        margins = [m0] + rules.fade_levels(m0, m_avg, n)[1:]

    da_pcts = [da_pct] * n
    # Capex & Sales-to-Capital reinvestment linkage:
    # 1) High-capex peak -> fade down to sustainable steady-state reinvestment ratio.
    # 2) Low-capex / IFRS 16 (D&A >> CapEx because ROU lease amortization sits in D&A while lease
    #    repayments sit in Financing CF) -> floor reinvestment at D&A and fade to max(D&A, steady)
    #    so lease depreciation is never treated as perpetual free cash flow.
    # 3) Sales-to-Capital linkage -> during elevated growth (g_t > terminal_g + 3pp), lift under-modeled
    #    reinvestment toward ΔRevenue / Sales-to-Capital so high growth is never modeled as "free".
    steady_capex_pct = da_pct + (terminal_g / ronic) * max(margin_target, 0.0) - nwc_pct * terminal_g
    steady_capex_pct = max(steady_capex_pct, da_pct)
    if cpx_pct > steady_capex_pct + 0.01:
        capex_pcts = rules.fade_levels(cpx_pct, steady_capex_pct, n)
        gaps.append(Gap("capex_fade", years=len(cpx_hist) or 1, cpx=cpx_pct, da=da_pct, steady=steady_capex_pct))
    elif cpx_pct < da_pct - 0.005:
        capex_pcts = rules.fade_levels(da_pct, steady_capex_pct, n)
        gaps.append(Gap("capex_reinvestment_floor", cpx=cpx_pct, da=da_pct, target=steady_capex_pct))
    else:
        capex_pcts = [cpx_pct] * n

    s2c_raw = (rev / ic) if (ic and ic > 0) else None
    s2c = max(0.5, min(4.0, s2c_raw)) if s2c_raw is not None else None
    s2c_adjusted = False
    if s2c is not None:
        cap_hi = max(cpx_pct * 1.5, da_pct + 0.25)
        for idx in range(n):
            g_t = growths[idx]
            if g_t > terminal_g + 0.03:
                req_net = g_t / s2c
                req_capex = da_pct + max(req_net - nwc_pct * g_t, 0.0)
                req_capex = min(req_capex, cap_hi)
                if req_capex > capex_pcts[idx] + 0.01:
                    capex_pcts[idx] = max(da_pct, 0.5 * capex_pcts[idx] + 0.5 * req_capex)
                    s2c_adjusted = True
        if s2c_adjusted:
            gaps.append(Gap("sales_to_capital_reinvestment", s2c=s2c, avg_capex=sum(capex_pcts) / n))

    # Stock-Based Compensation (SBC) & ongoing RSU dilution check:
    # GAAP/IFRS EBIT already expenses SBC; here we surface material SBC (>=2% of revenue) and
    # ensure net share dilution from un-offset RSU grants is reflected in share count floor.
    sbc_val = abs(_val(cf_a, d0, "Stock Based Compensation", "StockBasedCompensation") or 0.0)
    sbc_pct = (sbc_val / rev) if (sbc_val and rev) else 0.0
    sbc_mkt_pct = ((sbc_val / 1e6) / e_mkt_model) if (sbc_val and e_mkt_model) else 0.0
    sh_hist = [v / 1e6 for v in (float(_val(bs_a, d, "Ordinary Shares Number", "Share Issued") or 0) for d in dates[:4]) if v]
    net_share_cagr = 0.0
    if len(sh_hist) >= 2 and sh_hist[-1] > 0:
        net_share_cagr = (sh_hist[0] / sh_hist[-1]) ** (1.0 / (len(sh_hist) - 1)) - 1.0
    if sbc_pct >= 0.02:
        gaps.append(Gap("sbc_material", pct=sbc_pct, mkt_pct=sbc_mkt_pct, dil=net_share_cagr))
        if net_share_cagr > 0.005:
            # Conservative ongoing net RSU dilution drag over 5 years (capped at +10% shares)
            dil_mult = min(1.10, (1.0 + min(net_share_cagr, 0.03)) ** 5)
            shares_m = shares_m * dil_mult
            gaps.append(Gap("sbc_dilution_applied", cagr=net_share_cagr, mult=dil_mult))

    growth_notes = [f"規則後備：{cons_note}；其後向終值 g {terminal_g:.2%} 凸形收斂"] + ["向終值增長收斂"] * (n - 1)
    margin_notes = [f"首年用最近年度 EBIT 利潤率 {m0:.2%}；其後向 {margin_target:.2%} 淡化"] * n
    re_note = (f"總 D&A 取 {len(da_pcts_hist) or 1} 年平均 {da_pct:.2%} 營收；"
               f"Capex 由 {capex_pcts[0]:.2%} 過渡至 {capex_pcts[-1]:.2%} 營收"
               + (f"（含 Sales-to-Capital {s2c:.2f}× 再投資連動）" if s2c_adjusted and s2c else "")
               + ("（歷史高峰向可持續水平過渡）" if capex_pcts[0] != capex_pcts[-1] else ""))
    llm_note = None

    if use_llm and client is not None and client.available():
        lib_guidance = list(snap.get("library_context") or [])[:5]
        facts = {
            "ticker": ticker, "company": snap.get("company_name"),
            "sector": info.get("sector"), "industry": info.get("industry"),
            "model_currency": info.get("financialCurrency") or mk["currency"],
            "price": snap["price"], "price_currency": info.get("currency") or mk["currency"],
            "forecast_years": n, "terminal_g_rule": terminal_g, "rf": float(snap["rf"]["value"]),
            "base_year": {"date": d0, "revenue_m": rev / 1e6, "ebit_m": ebit / 1e6,
                          "ebit_margin": round(m0, 4), "tax_rate": round(tax, 4),
                          "da_pct_revenue": round(da_pct, 4), "capex_pct_revenue": round(cpx_pct, 4)},
            "history": [{"date": d, "revenue_m": r / 1e6, "ebit_margin":
                         round((_op_income(is_a, d)[0] or 0) / r, 4)} for d, r in hist_rev],
            "consensus": rev_est, "rules_proposal": {"revenue_growth": growths, "ebit_margins": margins},
            "library_guidance": lib_guidance,
        }
        try:
            res = _propose_paths(client, facts)
        except Exception as e:  # never fail the run because of the AI step
            res, llm_note = None, Gap("ai_failed", error=str(e))
        if res is not None:
            ok, why = _validate_paths(res, growths, margins, terminal_g, n)
            if ok:
                growths = res["revenue_growth"]
                margins = res["ebit_margins"]
                if res.get("terminal_g") is not None:
                    terminal_g = min(float(res["terminal_g"]), float(snap["rf"]["value"]))
                growth_notes = [f"AI 提案（{client.model}）：{res.get('rationale', {}).get('growth') or cons_note}"] + \
                               ["依 AI 路徑"] * (n - 1)
                margin_notes = [f"AI 提案：{res.get('rationale', {}).get('margins') or '見來源'}"] * n
                snap["assumption_source"] = "llm"
                if lib_guidance:
                    gaps.append(Gap("t3_library_guidance_used", n=len(lib_guidance)))
            else:
                llm_note = Gap("ai_rejected", why=why)
        if llm_note:
            gaps.append(llm_note)

    # ---- shares / capex floor (same rules as the hand-built specs)
    dil_rate = max(0.01, min(net_share_cagr, 0.04)) if net_share_cagr > 0 else 0.01
    shf = rules.shares_floor(shares_m, sh_hist, years=n, dilution=dil_rate)
    cpx_ppe = [abs(_val(cf_a, d, "Depreciation") or 0) / r for d, r in hist_rev if _val(cf_a, d, "Depreciation")]
    floor = rules.capex_floor(cpx_ppe, cpx_hist)
    floor_pct = max(floor["floor_pct"] if floor["floor_pct"] else cpx_pct, da_pct)
    # The 9% floor scenario must not carry the capex super-cycle for ever: with
    # BYD's 18.8% the floor came out at -18 HKD per share, which is not a floor.
    if floor_pct > steady_capex_pct:
        gaps.append(Gap("floor_steady", floor=floor_pct, steady=steady_capex_pct))
        floor_pct = steady_capex_pct

    # ---- FX: model currency (statements) vs market currency (price)
    val_date = dt.date.fromisoformat(snap.get("valuation_date") or dt.date.today().isoformat())
    base_end = dt.date.fromisoformat(d0)
    years, discs, periods = [], [], [1.0] * n
    end_0 = dt.date(base_end.year + 1, base_end.month, min(base_end.day, 28))
    days_left_0 = (end_0 - val_date).days
    full_yr1_rev = rev / 1e6 * (1 + growths[0])
    first_is_stub = False
    stub_fields = None
    ytd_actual_rev = None
    first_delta_nwc = None
    first_revenue = full_yr1_rev

    # If >3 months of the first forecast year have already elapsed on val_date, count only the remaining stub
    # (otherwise an entire nearly-finished year of cash flows is added undiscounted at t=0).
    if val_date > base_end and 1 <= days_left_0 < 275:
        frac = max(days_left_0, 15) / 365.25
        first_is_stub = True
        periods[0] = round(frac, 4)
        first_revenue = full_yr1_rev * frac
        ytd_actual_rev = full_yr1_rev - first_revenue
        stub_fields = {"h1_actual_revenue": ytd_actual_rev, "fy2026_revenue": full_yr1_rev}
        first_delta_nwc = nwc_pct * (full_yr1_rev - rev / 1e6) * frac
        years = [f"FY{end_0.year}（剩餘 {round(frac * 12, 1):g} 個月）"] + [f"FY{end_0.year + i}" for i in range(1, n)]
        discs = [frac / 2.0] + [frac + 0.5 + (i - 1) for i in range(1, n)]
        gaps.append(Gap("first_year_stub", year=f"FY{end_0.year}", frac=frac))
    elif days_left_0 <= 0:
        # Valuation date is already past the first forecast year end (annual report not yet filed):
        # discount forward from val_date using standard mid-year periods (0.5, 1.5, …).
        years = [f"FY{base_end.year + 1 + i}" for i in range(n)]
        discs = [0.5 + i for i in range(n)]
    else:
        for i in range(n):
            end = dt.date(base_end.year + 1 + i, base_end.month, min(base_end.day, 28))
            years.append(f"FY{end.year}")
            mid = dt.date(end.year, end.month, 1) - dt.timedelta(days=1) - dt.timedelta(days=182)
            discs.append(rules.midyear_t(val_date, mid))

    bridge = []
    if cash:
        bridge.append({"item": "現金及短期投資", "value": cash / 1e6, "sign": "+",
                       "basis": f"市場數據：{bs0} 現金及短期投資，全數加回（未扣最低營運現金）",
                       "source_name": "Yahoo Finance balance sheet", "url": _url(ticker, "balance-sheet"),
                       "data_date": bs0})
    if debt:
        bridge.append({"item": "計息債務（含租賃）", "value": debt / 1e6, "sign": "-",
                       "basis": f"市場數據：{bs0} 總債務（含資本租賃）", "source_name": "Yahoo Finance balance sheet",
                       "url": _url(ticker, "balance-sheet"), "data_date": bs0})
    if dtl:
        gaps.append(Gap("dtl_not_deducted", m=dtl / 1e6))
    if nci:
        bridge.append({"item": "非控股權益", "value": abs(nci) / 1e6, "sign": "+" if nci < 0 else "-",
                       "basis": f"市場數據：{bs0} 非控股權益 {nci / 1e6:.3f}m",
                       "source_name": "Yahoo Finance balance sheet", "url": _url(ticker, "balance-sheet"),
                       "data_date": bs0})

    if lt_eq:
        bridge.append({"item": "聯營及合營公司投資（帳面）", "value": abs(lt_eq) / 1e6, "sign": "+",
                       "basis": f"市場數據：{bs0} 長期股權投資（聯營＋合營），帳面值作市值近似；其收益不在營業利潤內", "source_name": "Yahoo Finance balance sheet",
                       "url": _url(ticker, "balance-sheet"), "data_date": bs0})
    if fin_assets:
        bridge.append({"item": "非流動金融資產（帳面）", "value": abs(fin_assets) / 1e6, "sign": "+",
                       "basis": f"市場數據：{bs0} 非流動金融資產投資，帳面值作市值近似", "source_name": "Yahoo Finance balance sheet",
                       "url": _url(ticker, "balance-sheet"), "data_date": bs0})
    if pref:
        bridge.append({"item": "優先股", "value": abs(pref) / 1e6, "sign": "-",
                       "basis": f"市場數據：{bs0} 優先股權益，屬非普通股東權益", "source_name": "Yahoo Finance balance sheet",
                       "url": _url(ticker, "balance-sheet"), "data_date": bs0})
    if pension:
        bridge.append({"item": "退休金及離職後福利負債", "value": abs(pension) / 1e6, "sign": "-",
                       "basis": f"市場數據：{bs0} 界定福利計劃淨負債（類債務項）", "source_name": "Yahoo Finance balance sheet",
                       "url": _url(ticker, "balance-sheet"), "data_date": bs0})
    if provisions and aro_industry:
        bridge.append({"item": "長期準備（棄置／復原義務）", "value": abs(provisions) / 1e6, "sign": "-",
                       "basis": f"市場數據：{bs0} 長期準備；{industry} 行業主要為資產棄置及環境復原義務，未來現金流出不在 FCFF 預測內，屬類債務項",
                       "source_name": "Yahoo Finance balance sheet", "url": _url(ticker, "balance-sheet"), "data_date": bs0})
    elif provisions:
        gaps.append(Gap("provisions_not_deducted", m=provisions / 1e6))
    gaps.append(Gap("forecast_start", year=str(years[0]), base=str(d0), end=base_end.isoformat()))
    gaps.append(Gap("no_segments"))

    cons_growths: list = [None] * n
    if g0 is not None:
        cons_growths[0] = g0
    if g1 is not None and n > 1:
        cons_growths[1] = g1

    return {
        "asset": snap.get("company_name") or ticker,
        "ticker": ticker,
        "market": mk["market"],
        "currency": model_ccy,
        "units": f"百萬 {model_ccy}；每股理論價 {mkt_ccy}",
        "valuation_date": snap.get("valuation_date"),
        "price": snap["price"], "price_date": snap["price_date"], "price_url": snap.get("price_url"),
        "price_source": "Yahoo Finance",
        "convention": (f"年中折現；估值日 {snap.get('valuation_date')}（澳門）。基準財年 {d0}，"
                       f"預測 {years[0]}–{years[-1]}（10 年）。模型貨幣 {model_ccy}"
                       + (f"（報表貨幣），每股理論價按 {model_ccy}→{mkt_ccy} 匯率 {fx_model_to_mkt:.4f} 換算"
                          if model_ccy != mkt_ccy else "") +
                       "。本檔為自動建立的通用規格。"),
        "forecast_years_label": f"{years[0]}–{years[-1]}（10 年）",
        "horizon_rationale": "通用規則：10 年預測期後收斂至終值增長；未有公司特定周期判斷。",
        "shares_m": shares_m,
        "shares_source": D(shares_m, shares_basis, "Yahoo Finance statements", _url(ticker, "balance-sheet"), bs0),
        "debt_m": (debt or 0.0) / 1e6,
        "D_basis": f"市場數據：{bs0} 總債務 {((debt or 0) / 1e6):.3f}m（含資本租賃）",
        "D_source": "Yahoo Finance balance sheet", "D_url": _url(ticker, "balance-sheet"), "D_date": bs0,
        "E_basis": f"市值 = {snap['price']} × {shares_m:.6f}m 股（{snap['price_date']} 收市，{mkt_ccy}）× 匯率 {fx_mkt_to_model:.4f} 換為 {model_ccy}",
        "rf": rf, "crp": crp,
        "kd_pre": D(kd, f"市場數據：利息費用 {interest / 1e6 if interest else 0:.3f}m ÷ 總債務 {(debt or 0) / 1e6:.3f}m ＝ {kd:.2%}"
                    if interest and debt else f"規則假設：Rf＋違約息差 ＝ {kd:.2%}",
                    "Yahoo Finance statements", _url(ticker, "financials"), d0),
        "tax_rate": marginal,
        "tax_rates": rules.fade_levels(tax, marginal, n),
        "tax_d": D(marginal, f"邊際稅率 {marginal:.2%}（{country or '未知地區'}法定稅率）：用於利息稅盾與 β 再槓桿；"
                   f"NOPAT 由最近年度有效稅率 {tax:.2%} 逐年過渡至邊際稅率", MARGINAL_TAX_SOURCE,
                   _url(ticker, "financials"), d0),
        "beta_reg": info.get("beta"),
        "beta_reg_url": _url(ticker, "key-statistics"),
        "beta_reg_date": snap.get("price_date") or "",
        "year_labels": years, "period_lengths": periods, "discount_periods": discs,
        "growths": growths, "margins": margins, "da_pcts": da_pcts, "capex_pcts": capex_pcts,
        "dnwc_pct": nwc_pct,
        "dnwc_rule": f"ΔNWC＝{nwc_pct:.1%}×ΔRevenue（{bs0} 應收＋存貨−應付−遞延收入 ÷ 營收；負值截斷為 0%）",
        "prev_revenue_for_first": rev / 1e6,
        "first_revenue": first_revenue,
        "first_delta_nwc": first_delta_nwc,
        "first_is_stub": first_is_stub,
        "stub_fields": stub_fields,
        "ytd_actual_revenue": ytd_actual_rev,
        "full_year_after_first": full_yr1_rev if first_is_stub else None,
        "static_keeps_first_revenue": first_is_stub,
        "static_base_revenue": rev / 1e6,
        "full_year_base_revenue": rev / 1e6,
        "growth_base_notes": growth_notes,
        "growth_rationales": growth_notes,
        "margin_rationales": margin_notes,
        "reinvestment_rationales": [re_note] * n,
        "row_sources": [_url(ticker, "financials"), _url(ticker, "balance-sheet"),
                        _url(ticker, "cash-flow"), _url(ticker, "analysis")],
        "terminal_g": terminal_g, "ronic": ronic, "roic_hist": roic, "wacc_estimate": wacc_est,
        "g_rationale": f"終值 g＝{terminal_g:.2%}，不高於{model_ccy} Rf {float(snap['rf']['value']):.2%}；通用規則假設。",
        "method_rationale": f"價值驅動終值：{ronic_note}。",
        "tv_method": "value_driver",
        "bridge": bridge,
        "fx_rate": fx_model_to_mkt,
        "fx_to_model_for_market_cap": fx_mkt_to_model,
        "fx_if_any": {
            "pair": f"{model_ccy}/{mkt_ccy}", "rate": fx_model_to_mkt,
            "applied": f"報表貨幣 {model_ccy}；交易貨幣 {mkt_ccy}",
            "per_share_fx": fx_model_to_mkt,
            "market_cap_fx": fx_mkt_to_model,
        },
        "corrections": [{"item": "規格來源", "requested": "不可讓模型憑記憶填數",
                         "adopted": "自動通用規格：數字全部取自 Yahoo 財報與 Damodaran 檔案",
                         "reason": "未經人工覆核，信心低於內建規格；引用前須核對年報"}],
        "base_year": {
            "year": f"FY{base_end.year}", "year_end": d0, "currency": f"{model_ccy} millions",
            "revenue": rev / 1e6, "ebit": ebit / 1e6, "ebit_margin": m0, "da": (da or 0) / 1e6,
            "capex": (capex or 0) / 1e6, "tax_rate_effective": tax, "tax_rate_marginal": marginal,
            "roic_operating": roic, "bridge_date": bs0,
            "revenue_row": rev_name, "ebit_row": ebit_name,
            "sources": [_url(ticker, "financials"), _url(ticker, "balance-sheet")],
        },
        "capex_floor_pct": floor_pct,
        "capex_floor_detail": floor,
        "shares_floor_m": shf["floor_shares_m"],
        "shares_floor_detail": shf,
        "bear_g_delta": -0.03, "bull_g_delta": 0.03,
        "bear_m_delta": -0.02, "bull_m_delta": 0.02,
        "consensus_growths": cons_growths,
        "growth_table_notes": [cons_note, "通用規格：一致預期只取 Yahoo 0y／+1y，其後無公開數字，留空。",
                               "悲觀／樂觀：首 4 個預測年增長 ±3pp、利潤率 ±2pp。"],
        "growth_table_sources": [{"name": f"Yahoo Finance Analysis {ticker}",
                                  "url": _url(ticker, "analysis"), "data_date": snap.get("price_date")}],
        "ib_compare": snap.get("ib_compare") or {},
        "cfo_fcff_crosscheck": _crosscheck(is_a, cf_a, d0, rev, ebit, tax, da, capex, interest, ticker),
        "data_gaps": gaps,
        "assumption_source": snap.get("assumption_source") or "rules_fallback",
        "llm_status": snap.get("llm_status") or {},
        "generic_spec": True,
        "generic_industry": industry,
        "confidence": "low",
        "method_warning": method_warning,
    }


def _url(ticker: str, page: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}/{page}"


def _crosscheck(is_a, cf_a, d0, rev, ebit, tax, da, capex, interest, ticker) -> dict:
    cfo = _val(cf_a, d0, "Operating Cash Flow")
    if cfo is None:
        return {}
    fcff_cfo = cfo + (interest or 0) * (1 - tax) - (capex or 0)
    return {
        "year": f"FY{d0[:4]}",
        "cfo": cfo / 1e6, "interest": (interest or 0) / 1e6, "tax": tax, "capex": (capex or 0) / 1e6,
        "fcff_cfo_formula": fcff_cfo / 1e6,
        "fcff_cfo_note": "FCFF≈CFO+利息×(1−t)−Capex（利息在融資現金流）",
        "nopat": ebit * (1 - tax) / 1e6, "da": (da or 0) / 1e6,
        "note": "NOPAT 路徑另減 ΔNWC；差額主要來自營運資金與非現金項目。預測以 NOPAT 為主。",
        "source": _url(ticker, "cash-flow"), "data_date": d0,
    }


def _propose_paths(client, facts: dict):
    """Ask the configured model for a path, through the single LLM client."""
    from ..llm import steps as llm_steps

    import json as _json
    res = llm_steps.propose_assumptions(client, _json.dumps(facts, ensure_ascii=False, default=str))
    if not res.ok or not isinstance(res.data, dict):
        raise RuntimeError(res.error or "回覆不是 JSON 物件")
    return res.data


def _validate_paths(res: dict, rules_growths: list, rules_margins: list, terminal_g: float,
                    n: int) -> tuple[bool, str]:
    """Reject AI output that is malformed or drifts away from the data-driven path.

    The engine must never receive invented numbers, so the AI may only shape the
    path inside a band around the rules result.
    """
    g = res.get("revenue_growth")
    m = res.get("ebit_margins")
    if not isinstance(g, list) or len(g) != n:
        return False, Gap("ai_bad_length", field="revenue_growth", n=n)
    if not isinstance(m, list) or len(m) != n:
        return False, Gap("ai_bad_length", field="ebit_margins", n=n)
    try:
        g = [float(x) for x in g]
        m = [float(x) for x in m]
    except (TypeError, ValueError):
        return False, Gap("ai_not_numbers")
    if any(math.isnan(x) or x < -0.6 or x > 3.0 for x in g):
        return False, Gap("ai_growth_range")
    if any(math.isnan(x) or x < -0.5 or x > 0.95 for x in m):
        return False, Gap("ai_margin_range")
    if abs(g[0] - rules_growths[0]) > 0.12:
        return False, Gap("ai_first_growth", g=g[0], rules=rules_growths[0])
    if abs(m[0] - rules_margins[0]) > 0.10:
        return False, Gap("ai_first_margin", m=m[0], rules=rules_margins[0])
    tg = res.get("terminal_g")
    if tg is not None:
        # A non-numeric value here used to raise outside the AI try-block and
        # fail the whole valuation; reject it like any other malformed proposal.
        try:
            tg = float(tg)
        except (TypeError, ValueError):
            return False, Gap("ai_tg_not_number", tg=repr(tg))
        if math.isnan(tg) or tg < -0.02:
            return False, Gap("ai_tg_range", tg=str(tg))
        if tg > terminal_g + 1e-9:
            return False, Gap("ai_tg_above", tg=tg, cap=terminal_g)
        res["terminal_g"] = tg
    res["revenue_growth"] = g
    res["ebit_margins"] = m
    return True, "ok"
