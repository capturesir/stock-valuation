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
from typing import Optional

from ..engine import method_a as MA
from ..engine.fcff_engine import D
from ..fetch import yahoo
from . import rules

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
        gaps.append(
            f"行業按 Yahoo「{info.get('industry') or info.get('sector')}」對應 Damodaran「{industry}」；"
            "未逐公司核對 indname.xls，若分類有誤會改變 β 與 WACC。"
        )
        return industry

    # No Damodaran industry matched: derive the unlevered beta from the regression beta.
    reg = info.get("beta")
    if not reg or d_over_e <= 0:
        raise GenericSpecError(
            f"{ticker} 無法對應 Damodaran 行業（Yahoo 行業：{info.get('industry') or '無'}），"
            "亦無可用迴歸 Beta，無法按方法 A 計算折現率。"
        )
    bu = float(reg) / (1 + (1 - tax) * d_over_e)
    label = f"未分類（{info.get('industry') or info.get('sector') or ticker}）"
    MA.register_industry(
        label, bu, bu,
        f"方法 A 例外：Yahoo 迴歸 Beta {reg} 按 D/E {d_over_e:.3f}、稅率 {tax:.1%} 還原為 βU {bu:.4f}；"
        "未能對應 Damodaran 行業，屬估算值。",
    )
    MA.register_ticker(ticker, label)
    gaps.append(f"未能對應 Damodaran 行業，β 以 Yahoo 迴歸 Beta 還原（βU≈{bu:.3f}），與方法 A 標準不同，屬估算值。")
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
    for n in names:
        v = _val(block, date, n)
        if v is not None:
            return v, n
    return None, None


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
    gaps.append(
        "本檔為【自動建立的通用規格】：全部數字取自 Yahoo 財報與 Damodaran 檔案，未經人工逐項覆核；"
        "引用前請自行核對年報與最新公告，並優先以內建規格（NVDA／1830.HK）的標準重做。"
    )

    is_a, bs_a, cf_a = fund.get("is_a") or {}, fund.get("bs_a") or {}, fund.get("cf_a") or {}
    dates = sorted(is_a.keys(), reverse=True) if isinstance(is_a, dict) else []
    if len(dates) < 2:
        raise GenericSpecError(f"{ticker} Yahoo 年度財報不足（只取得 {len(dates)} 期），無法建立模型。")
    d0 = dates[0]
    bs0 = _latest(bs_a)

    rev, rev_name = _first(is_a, d0, ["Total Revenue", "Operating Revenue"])
    if not rev or rev <= 0:
        raise GenericSpecError(f"{ticker} 取不到年度營收，無法建立模型。")
    ebit, ebit_name = _first(is_a, d0, ["EBIT", "Operating Income", "Total Operating Income As Reported"])
    if ebit is None:
        raise GenericSpecError(f"{ticker} 取不到年度 EBIT／營業利潤，無法建立模型。")

    m0 = ebit / rev
    if ebit <= 0:
        raise GenericSpecError(
            f"{ticker} 最近年度 EBIT 為負（{(ebit / 1e6):,.1f} 百萬，約 {m0:.1%} 營收），"
            "本引擎的 FCFF 五情境模型假設正 NOPAT，對虧損公司會給出沒有意義（甚至負值）的理論價。"
            "此類公司需先建立虧損轉盈的專用模型（例如按分部或按現金消耗估算），不可用通用規格硬算。"
        )
    da, _ = _first(is_a, d0, ["Reconciled Depreciation"])
    if da is None:
        da, _ = _first(cf_a, d0, ["Depreciation And Amortization", "Depreciation Amortization Depletion", "Depreciation"])
    capex_neg, _ = _first(cf_a, d0, ["Capital Expenditure", "Capital Expenditure Reported", "Purchase Of PPE"])
    capex = abs(capex_neg) if capex_neg is not None else None
    ppe_dep, _ = _first(cf_a, d0, ["Depreciation"])
    tax = _val(is_a, d0, "Tax Rate For Calcs")
    if tax is None:
        pretax, provision = _val(is_a, d0, "Pretax Income"), _val(is_a, d0, "Tax Provision")
        tax = (provision / pretax) if pretax and provision is not None and pretax else None
    interest, _ = _first(is_a, d0, ["Interest Expense", "Interest Expense Non Operating"])
    shares = _val(bs_a, bs0, "Ordinary Shares Number", "Share Issued")
    if not shares:
        shares = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding")
    if not shares:
        raise GenericSpecError(f"{ticker} 取不到股數，無法換算每股理論價。")
    shares_m = float(shares) / 1e6

    cash, _ = _first(bs_a, bs0, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])
    if cash is None:
        c1, c2 = _val(bs_a, bs0, "Cash And Cash Equivalents"), _val(bs_a, bs0, "Other Short Term Investments")
        cash = (c1 or 0.0) + (c2 or 0.0) if (c1 or c2) else None
    debt, _ = _first(bs_a, bs0, ["Total Debt"])
    if debt is None:
        parts = [_val(bs_a, bs0, "Long Term Debt"), _val(bs_a, bs0, "Current Debt"),
                 _val(bs_a, bs0, "Capital Lease Obligations")]
        debt = sum(p for p in parts if p) or None
    dtl, _ = _first(bs_a, bs0, "Non Current Deferred Taxes Liabilities")
    nci, _ = _first(bs_a, bs0, "Minority Interest")

    for label, v in (("D&A", da), ("資本開支", capex), ("無風險利率", snap.get("rf", {}).get("value"))):
        if v is None:
            gaps.append(f"Yahoo 未提供{label}，已用替代值或規則假設，請核對年報。")

    tax = 0.25 if tax is None else max(0.0, min(float(tax), 0.5))
    da_pct = (da / rev) if da else 0.05
    cpx_pct = (capex / rev) if capex else max(da_pct, 0.02)

    # working capital: AR + inventory - AP - deferred revenue
    ar = _val(bs_a, bs0, "Accounts Receivable") or 0.0
    inv = _val(bs_a, bs0, "Inventory") or 0.0
    ap = _val(bs_a, bs0, "Accounts Payable") or 0.0
    defrev = _val(bs_a, bs0, "Current Deferred Revenue") or 0.0
    nwc_pct = (ar + inv - ap - defrev) / rev

    # history for percentages and growth
    hist_rev = [(dt, _val(is_a, dt, "Total Revenue", "Operating Revenue")) for dt in dates[:4]]
    hist_rev = [(d, v) for d, v in hist_rev if v]
    rev_cagr = None
    if len(hist_rev) >= 2:
        n_years = len(hist_rev) - 1
        rev_cagr = (hist_rev[0][1] / hist_rev[-1][1]) ** (1 / n_years) - 1
    margin_hist = [(dt_, _val(is_a, dt_, "EBIT", "Operating Income")) for dt_, _ in hist_rev]
    margins_hist = [v / r for (_, r), (_, v) in zip(hist_rev, margin_hist) if v is not None and r]
    m_avg = sum(margins_hist) / len(margins_hist) if margins_hist else m0
    da_hist = [(dt_, _val(is_a, dt_, "Reconciled Depreciation")) for dt_, _ in hist_rev]
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
    if code not in MA.CRP:
        gaps.append(f"Damodaran CRP 未收錄 {code} 市場，暫用美國 CRP＝0，請人工確認國家風險。")
        code = "US"
    crp = MA.crp(code, "（通用規格：按上市地推定風險來源，港股若收入主要來自內地應改用 CRP 中國）"
                 if code == "HK" else "")
    e_mkt_model = snap["price"] * fx_mkt_to_model * shares_m

    kd = (interest / debt) if interest and debt and debt > 0 else None
    if kd is None or not (0 < kd < 0.5):
        kd = snap["rf"]["value"] + MA.DEFAULT_SPREAD.get(code, 0.005)
        gaps.append("利息費用／總債務不可用，Kd 以 Rf＋Damodaran 該國違約息差代替。")
    d_over_e = (debt or 0.0) / e_mkt_model if e_mkt_model else 0.0

    industry = resolve_industry(ticker, info, tax, d_over_e, gaps)

    # ---- forecast paths (rules), then optional AI proposal
    n = 10
    terminal_g = min(mk["terminal_g"], float(snap["rf"]["value"]))
    rev_est = fund.get("rev_est") or {}
    g0 = g1 = None
    cons_note = "Yahoo 無營收一致預期，採用歷史增長。"
    try:
        y0 = (rev_est.get("0y") or {})
        y1 = (rev_est.get("+1y") or {})
        prev_rev0 = y0.get("yearAgoRevenue")
        if y0.get("avg") and prev_rev0:
            g0 = float(y0["avg"]) / float(prev_rev0) - 1
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
        gaps.append(f"無營收共識，首年增長用近 {len(hist_rev) - 1} 年營收 CAGR {g0:.2%}。")
    # consensus series can jump wildly (e.g. a low base year); clamp before use
    def _clamp_g(x, label):
        lo, hi = -0.5, 1.5
        if x is not None and not (lo <= x <= hi):
            gaps.append(f"營收共識{label} {x:.1%} 超出合理範圍（{lo:.0%}～{hi:.0%}），已截斷為邊界值。")
            return max(min(x, hi), lo)
        return x

    g0 = _clamp_g(g0, "首年")
    g1 = _clamp_g(g1, "次年")
    starts = [float(g0)] + ([float(g1)] if g1 is not None else [])
    growths = rules.fade_to_g(starts, terminal_g, n)
    margins = [m0] + rules.fade_levels(m0, m_avg, n)[1:]
    da_pcts = [da_pct] * n
    capex_pcts = [cpx_pct] * n
    growth_notes = [f"規則後備：{cons_note}；其後向終值 g {terminal_g:.2%} 直線淡化"] + ["向終值增長直線淡化"] * (n - 1)
    margin_notes = [f"首年用最近年度 EBIT 利潤率 {m0:.2%}；其後向 {len(margins_hist)} 年平均 {m_avg:.2%} 淡化"] * n
    re_note = f"總 D&A 取 {len(da_pcts_hist) or 1} 年平均 {da_pct:.2%} 營收；Capex 取 {len(cpx_hist) or 1} 年平均 {cpx_pct:.2%} 營收"
    llm_note = None

    if use_llm and client is not None and client.available():
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
                         round((_val(is_a, d, "EBIT", "Operating Income") or 0) / r, 4)} for d, r in hist_rev],
            "consensus": rev_est, "rules_proposal": {"revenue_growth": growths, "ebit_margins": margins},
        }
        try:
            res = _propose_paths(client, facts)
        except Exception as e:  # never fail the run because of the AI step
            res, llm_note = None, f"AI 提案失敗：{e}"
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
            else:
                llm_note = f"AI 提案未採用：{why}"
        if llm_note:
            gaps.append(llm_note)

    # ---- shares / capex floor (same rules as the hand-built specs)
    sh_hist = [v / 1e6 for v in (float(_val(bs_a, d, "Ordinary Shares Number", "Share Issued") or 0) for d in dates[:4]) if v]
    shf = rules.shares_floor(shares_m, sh_hist, years=n, dilution=0.01)
    cpx_ppe = [abs(_val(cf_a, d, "Depreciation") or 0) / r for d, r in hist_rev if _val(cf_a, d, "Depreciation")]
    floor = rules.capex_floor(cpx_ppe, cpx_hist)
    floor_pct = floor["floor_pct"] if floor["floor_pct"] else cpx_pct

    # ---- FX: model currency (statements) vs market currency (price)
    val_date = dt.date.fromisoformat(snap.get("valuation_date") or dt.date.today().isoformat())
    base_end = dt.date.fromisoformat(d0)
    years, discs = [], []
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
        bridge.append({"item": "遞延稅項負債", "value": dtl / 1e6, "sign": "-",
                       "basis": f"市場數據：{bs0} 非流動遞延稅負債", "source_name": "Yahoo Finance balance sheet",
                       "url": _url(ticker, "balance-sheet"), "data_date": bs0})
    if nci:
        bridge.append({"item": "非控股權益", "value": abs(nci) / 1e6, "sign": "+" if nci < 0 else "-",
                       "basis": f"市場數據：{bs0} 非控股權益 {nci / 1e6:.3f}m",
                       "source_name": "Yahoo Finance balance sheet", "url": _url(ticker, "balance-sheet"),
                       "data_date": bs0})

    gaps.append(
        f"預測自 {years[0]} 起（基準財年 {d0} 已入帳）；財年結算日按 {base_end.isoformat()} 推定，"
        "若公司改變年結日或已公布中期業績，須重跑。"
    )
    gaps.append("通用規格未做分部、地域收入拆分；CRP 按上市地推定，未按風險來源調整。")

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
        "shares_source": D(shares_m, f"市場數據：{bs0} 已發行股數 {shares / 1e6:.3f}m（Yahoo balance sheet）",
                           "Yahoo Finance balance sheet", _url(ticker, "balance-sheet"), bs0),
        "debt_m": (debt or 0.0) / 1e6,
        "D_basis": f"市場數據：{bs0} 總債務 {((debt or 0) / 1e6):.3f}m（含資本租賃）",
        "D_source": "Yahoo Finance balance sheet", "D_url": _url(ticker, "balance-sheet"), "D_date": bs0,
        "E_basis": f"市值 = {snap['price']} × {shares_m:.6f}m 股（{snap['price_date']} 收市，{mkt_ccy}）× 匯率 {fx_mkt_to_model:.4f} 換為 {model_ccy}",
        "rf": rf, "crp": crp,
        "kd_pre": D(kd, f"市場數據：利息費用 {interest / 1e6 if interest else 0:.3f}m ÷ 總債務 {(debt or 0) / 1e6:.3f}m ＝ {kd:.2%}"
                    if interest and debt else f"規則假設：Rf＋違約息差 ＝ {kd:.2%}",
                    "Yahoo Finance statements", _url(ticker, "financials"), d0),
        "tax_rate": tax,
        "tax_d": D(tax, f"市場數據：最近年度有效稅率 {tax:.2%}（Yahoo Tax Rate For Calcs／稅項÷稅前）",
                   "Yahoo Finance statements", _url(ticker, "financials"), d0),
        "beta_reg": info.get("beta"),
        "beta_reg_url": _url(ticker, "key-statistics"),
        "beta_reg_date": snap.get("price_date") or "",
        "year_labels": years, "period_lengths": [1.0] * n, "discount_periods": discs,
        "growths": growths, "margins": margins, "da_pcts": da_pcts, "capex_pcts": capex_pcts,
        "dnwc_pct": nwc_pct,
        "dnwc_rule": f"ΔNWC＝{nwc_pct:.1%}×ΔRevenue（{bs0} 應收＋存貨−應付−遞延收入 ÷ 營收）",
        "prev_revenue_for_first": rev / 1e6,
        "first_revenue": rev / 1e6 * (1 + growths[0]),
        "first_is_stub": False,
        "static_keeps_first_revenue": False,
        "static_base_revenue": rev / 1e6,
        "growth_base_notes": growth_notes,
        "growth_rationales": growth_notes,
        "margin_rationales": margin_notes,
        "reinvestment_rationales": [re_note] * n,
        "row_sources": [_url(ticker, "financials"), _url(ticker, "balance-sheet"),
                        _url(ticker, "cash-flow"), _url(ticker, "analysis")],
        "terminal_g": terminal_g, "ronic": 0.12,
        "g_rationale": f"終值 g＝{terminal_g:.2%}，不高於{model_ccy} Rf {float(snap['rf']['value']):.2%}；通用規則假設。",
        "method_rationale": "價值驅動終值：RONIC＝12%（通用規則假設，非公司指引）。",
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
            "capex": (capex or 0) / 1e6, "tax_rate_effective": tax, "bridge_date": bs0,
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
        return False, f"revenue_growth 長度不是 {n}"
    if not isinstance(m, list) or len(m) != n:
        return False, f"ebit_margins 長度不是 {n}"
    try:
        g = [float(x) for x in g]
        m = [float(x) for x in m]
    except (TypeError, ValueError):
        return False, "路徑含非數字"
    if any(math.isnan(x) or x < -0.6 or x > 3.0 for x in g):
        return False, "增長路徑超出 −60%～300% 合理範圍"
    if any(math.isnan(x) or x < -0.5 or x > 0.95 for x in m):
        return False, "利潤率路徑超出 −50%～95% 合理範圍"
    if abs(g[0] - rules_growths[0]) > 0.12:
        return False, f"首年增長 {g[0]:.1%} 與資料推算 {rules_growths[0]:.1%} 相差超過 12 個百分點"
    if abs(m[0] - rules_margins[0]) > 0.10:
        return False, f"首年利潤率 {m[0]:.1%} 與最近年度 {rules_margins[0]:.1%} 相差超過 10 個百分點"
    tg = res.get("terminal_g")
    if tg is not None and float(tg) > terminal_g + 1e-9:
        return False, f"終值 g {float(tg):.2%} 高於規則上限 {terminal_g:.2%}"
    res["revenue_growth"] = g
    res["ebit_margins"] = m
    return True, "ok"
