"""方法 A 折現率規則（用戶 2026-10-08 批准；全部已估值名單統一：原有 5 隻＋擴大覆蓋 10 隻，含第二階段參考值）。
Single source of the market inputs shared by every DCFF model (house *_fcff_dcf.py and coverage_fcff_dcf.py).

Rule (coverage/wacc_review.md §採用；README_daily_build.md「折現率方法 A」):
  Rf   = spot 10-year government yield of the MODEL currency on the valuation date (USD: UST 10y; JPY/KRW: local 10y minus the
         Damodaran rating-based default spread of that country, Damodaran's local-currency practice; default risk then enters via CRP).
  ERP  = Damodaran implied ERP published for the month of the valuation date (same month as Rf; Damodaran computes it against the
         UST 10y of the month start) – refreshed monthly (ERP / ERP_DATE / ERP_RF below).
  CRP  = Damodaran country risk premium (rating-based, latest update: January / July file). US-based names: 0 (ERP is the US ERP).
  Beta = Damodaran GLOBAL industry "unlevered beta corrected for cash", multi-year average column ("Average (2020-24)" in the
         2026-01-05 file), relevered at the company's market D/E and model tax rate: βL = βU × (1 + (1 − t) × D/E).
         Industry = Damodaran company classification (indname.xls). The regression beta (Yahoo 5Y monthly) is shown as a
         reference only; |regression − βL| > 0.5 is FLAGGED in the report (never used to override).
  Kd   = Rf + rating spread (unchanged per model); weights at market E and book debt (unchanged).
  g    = terminal growth must be <= Rf of the model currency (checked by check_g()).

Monthly refresh (first trading day after Damodaran posts the new month's implied ERP, usually the 1st–3rd):
  1. ERP, ERP_DATE, ERP_RF below <- https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm (implied ERP for the month)
  2. Rf of each model (house scripts: rf= line; coverage_fcff_dcf.py: CUR) <- spot 10y on the new valuation date
  3. January / July: CRP and DEFAULT_SPREAD <- Damodaran ctryprem file; January: BETA_U_AVG <- new betaGlobal.xls
  4. rerun models (house scripts + coverage_fcff_dcf.py --save); the 理論價 revision entry of a routine monthly Rf/ERP/CRP/beta
     data refresh uses fv_change_reason_type = data_update (method_change is reserved for a change of the RULE itself, e.g. the
     2026-10-08 adoption); then fv_history.py <date>, reports/CSVs, check_assumptions.py, verify_fcff.py,
     pending_removal_check.py <date> (--apply only for new non-exempt >= 2x hits).
Debt (2317.TW decision 2026-10-08): gross interest-bearing debt incl. short-term borrowings and leases is used in both the WACC
weight and the EV->equity bridge; working-capital short-term borrowings are NOT netted out unless the company discloses them as
self-liquidating trade finance whose cost sits inside EBIT (not the case for Hon Hai; see coverage/wacc_review.md §採用).
"""
from .fcff_engine import D

U_DAM = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm"
U_BETA = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betaGlobal.xls"
U_IND = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/indname.xls"
U_CRP = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJuly26.xlsx"
ADOPTED = "2026-10-08"

ERP, ERP_DATE, ERP_RF = 0.0420, "2026-10-01", 0.0529
# Damodaran ctrypremJuly26.xlsx, sheet "ERPs by country": Date of update 2026-07-01 (default spreads corrected 2026-07-09);
# rating-based country risk premium and default spread (mature market premium 4.20%, volatility multiplier 1.5545)
CRP_DATE = "2026-07-01"
CRP = {"US": (0.0, "Aa1", "美國"), "TW": (0.0074187, "Aa3", "台灣"), "HK": (0.0074187, "Aa3", "香港"), "CN": (0.0087279, "A1", "中國"),
       "JP": (0.0087279, "A1", "日本"), "KR": (0.0061095, "Aa2", "韓國")}
DEFAULT_SPREAD = {"US": 0.0021864, "TW": 0.0047724, "HK": 0.0047724, "CN": 0.0056146, "JP": 0.0056146, "KR": 0.0039302}
# Damodaran betaGlobal.xls (Date updated 2026-01-05), sheet "Industry Averages": unlevered beta corrected for cash,
# column "Average (2020-24)" (multi-year average; Damodaran: "less likely to be volatile over time") and the 2026 point value
BETA_U_AVG = {"Semiconductor": 1.6730813501833233, "Software (Entertainment)": 1.3119689448756404,
              "Oil/Gas (Production and Exploration)": 0.9855362696227941, "Electronics (General)": 1.3330271046628153,
              "Drugs (Pharmaceutical)": 0.966072431006604, "Electrical Equipment": 1.1408548226983006, "Entertainment": 1.0317276888937115,
              "Auto Parts": 1.2628645805444214, "Telecom (Wireless)": 0.6394706110438017,
              "Retail (Special Lines)": 0.9659582525932573}
BETA_U_POINT = {"Semiconductor": 1.8688970322549403, "Software (Entertainment)": 1.2346330983114904,
                "Oil/Gas (Production and Exploration)": 0.7463854591601625, "Electronics (General)": 1.568504800835242,
                "Drugs (Pharmaceutical)": 0.9987543035477139, "Electrical Equipment": 1.3406410283101367, "Entertainment": 0.9820773204991382,
                "Auto Parts": 1.3534718796622696, "Telecom (Wireless)": 0.6028420822440717,
                "Retail (Special Lines)": 0.9036386392039154}
# company -> Damodaran industry (indname.xls "By industry", 2026-01; verified 2026-10-08)
IND = {"2330.TW": "Semiconductor", "TSM": "Semiconductor", "MU": "Semiconductor", "AVGO": "Semiconductor",
       "GOOGL": "Software (Entertainment)", "0883.HK": "Oil/Gas (Production and Exploration)", "2317.TW": "Electronics (General)",
       "6981.T": "Electronics (General)", "009150.KS": "Electronics (General)", "LLY": "Drugs (Pharmaceutical)",
       "5803.T": "Electrical Equipment", "010120.KS": "Electrical Equipment", "7974.T": "Entertainment", "012330.KS": "Auto Parts",
       "0941.HK": "Telecom (Wireless)", "1883.HK": "Telecom (Wireless)", "0178.HK": "Retail (Special Lines)", "9896.HK": "Retail (Special Lines)"}
FLAG_GAP = 0.5



def register_industry(name, beta_u_avg, beta_u_point=None, citation=""):
    """Add or overwrite a Damodaran industry beta. Callers must cite betaGlobal.xls cell/date."""
    BETA_U_AVG[name] = beta_u_avg
    if beta_u_point is not None:
        BETA_U_POINT[name] = beta_u_point
    return {"industry": name, "beta_u_avg": beta_u_avg, "beta_u_point": BETA_U_POINT.get(name), "citation": citation}


def register_ticker(ticker, industry):
    if industry not in BETA_U_AVG:
        raise KeyError(f"Industry {industry!r} is not in BETA_U_AVG; register_industry() first")
    IND[ticker] = industry
    return IND[ticker]



def relever(bu, d_m, e_m, tax):
    return bu * (1 + (1 - tax) * d_m / e_m)


def erp():
    return D(ERP, f"市場數據：Damodaran {ERP_DATE} 隱含 ERP {ERP:.2%}（當月，對應美國 10 年期 {ERP_RF:.2%}）；方法 A：每月採用當月隱含 ERP",
             "Damodaran Online（Implied ERP）", U_DAM, ERP_DATE)


def crp(country, extra=""):
    v, rating, name = CRP[country]
    if country == "US":
        return D(0.0, "分析師假設（方法 A）：美國公司，ERP 已是美國隱含 ERP，不另加 CRP" + extra, "Damodaran Country Risk Premiums（2026-07 更新）", U_CRP, CRP_DATE)
    return D(v, f"市場數據：Damodaran {name} {rating} 國家風險溢價 {v:.2%}（2026-07-01 年中更新）" + extra, "Damodaran Country Risk Premiums（ctrypremJuly26.xlsx）", U_CRP, CRP_DATE)


def beta(ticker, d_m, e_m, tax, reg, reg_url, reg_date):
    """-> (beta_ref dict, beta_adopted dict, info). reg = Yahoo 5Y monthly regression beta or None."""
    ind = IND[ticker]; bu = BETA_U_AVG[ind]; bl = relever(bu, d_m, e_m, tax)
    blume = 0.67 * reg + 0.33 if reg is not None else None
    flag = reg is not None and abs(reg - bl) > FLAG_GAP
    ref_txt = (f"市場數據：Yahoo 個股迴歸 Beta（5Y 月度）{reg:.3f}（Blume 調整 {blume:.2f}）" if reg is not None else "市場數據：Yahoo 未提供個股 Beta（填 0 只作佔位，不參與計算）") + \
              f"；Damodaran 全球「{ind}」行業 βU（現金調整）多年平均 {bu:.3f}、2026 點值 {BETA_U_POINT[ind]:.3f}" + \
              (f"；⚠ 迴歸 β 與行業 β {bl:.2f} 相差 {abs(reg - bl):.2f} > 0.5（方法 A：只標示，不推翻）" if flag else "")
    ref = D(reg if reg is not None else 0.0, ref_txt, "Yahoo Finance key-statistics；Damodaran Betas（Global）", reg_url, reg_date)
    ado = D(round(bl, 4), f"方法 A（{ADOPTED} 用戶批准，全部名單統一）：Damodaran 全球「{ind}」行業 βU 多年平均（Average 2020-24）{bu:.3f} × (1 + (1 − {tax:.1%}) × D/E {d_m / e_m:.3f}) = {bl:.3f}；行業按 Damodaran 公司分類（indname.xls）",
            "Damodaran Betas（Global, 2026-01-05）；indname.xls", U_BETA, "2026-01-05")
    info = {"industry": ind, "beta_u_avg": bu, "beta_u_point": BETA_U_POINT[ind], "d_over_e": d_m / e_m, "beta_relevered": bl,
            "regression_beta": reg, "blume_beta": blume, "flag_gap_gt_0_5": flag, "gap": (reg - bl) if reg is not None else None}
    return ref, ado, info


def check_g(g, rf, ticker=""):
    if g > rf + 1e-12:
        raise SystemExit(f"[方法 A] {ticker} 終值 g {g:.2%} 高於模型貨幣 Rf {rf:.2%}")
    return True


def fix_revisions(out, date=ADOPTED):
    """fcff_engine.build_json appends the 2026-10-01 TV-timing note to every '理論價…' revision; strip it from later entries."""
    for r in out["revisions"]:
        if r.get("date") == date and str(r.get("item", "")).startswith("理論價"):
            r["reason"] = r["reason"].replace("；另含終值年中一致性修正（終值按 t_N 折現，見 spec_deviations）", "")
    out["method_a"] = dict(out.get("method_a") or {}, adopted=date, rule="coverage/wacc_review.md §採用")
    return out
