"""Build handbook-compliant model specs for the two validation names from fetched snapshots."""
from __future__ import annotations

import datetime as dt

from ..engine.fcff_engine import D
from ..engine import method_a as MA
from . import rules


VAL_DATE = "2026-11-11"
VAL = dt.date(2026, 11, 11)


def register_industries_from_damodaran():
    """Semiconductor already in method_a. Add Business & Consumer Services with file citation."""
    # From data/damodaran/betaGlobal.xls, sheet Industry Averages, Date updated 2026-01-05:
    # Industry Name row "Business & Consumer Services":
    #   Unlevered beta corrected for cash (2026 point col) = 0.9083303812347316
    #   Average (2020-24) = 0.9617623654591464
    citation = ("Damodaran betaGlobal.xls, sheet Industry Averages, Date updated 2026-01-05；"
                "列 Business & Consumer Services；欄 Unlevered beta corrected for cash＝0.9083303812347316，"
                "欄 Average (2020-24)＝0.9617623654591464；"
                "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betaGlobal.xls")
    if "Business & Consumer Services" not in MA.BETA_U_AVG:
        MA.register_industry("Business & Consumer Services", 0.9617623654591464, 0.9083303812347316, citation)
    # NVDA / 1830 classification from indname.xls By industry, same vintage:
    # NVIDIA Corporation (NasdaqGS:NVDA) -> Semiconductor, US
    # Perfect Medical Health Management Limited (SEHK:1830) -> Business & Consumer Services, Hong Kong
    if "NVDA" not in MA.IND:
        MA.register_ticker("NVDA", "Semiconductor")
    if "1830.HK" not in MA.IND:
        MA.register_ticker("1830.HK", "Business & Consumer Services")
    return citation


def _ymid(year, month, day=15):
    return dt.date(year, month, min(day, 28))


def spec_nvda(snap: dict) -> dict:
    register_industries_from_damodaran()
    gaps = list(snap.get("data_gaps") or [])
    # ---- sourced figures (USD millions unless noted) ----
    P = snap["price"]
    PDATE = snap["price_date"]
    # Q2 FY2027 period-end shares 24,147m; diluted WAS 24,285m; basic 24,190m
    # Base diluted ≈ period-end + (diluted WAS − basic WAS)
    SH = 24147.0 + (24285.0 - 24190.0)  # 24,242
    REV26 = 215938.0  # FY2026 ended ~2026-01-25 (Yahoo col 2026-01-31)
    EBIT26 = 130387.0  # Operating Income (GAAP)
    DA26 = 2843.0
    CPX26 = 6042.0
    TAX26 = 0.15117
    TAX = 0.165  # Q2 FY2027 Tax Rate For Calcs 16.5%; recent run-rate
    INT26 = 259.0
    Q1_REV, Q2_REV = 81615.0, 96221.0
    H1_REV = Q1_REV + Q2_REV  # 177836
    Q1_EBIT, Q2_EBIT = 53536.0, 63734.0
    H1_EBIT = Q1_EBIT + Q2_EBIT
    Q1_DA, Q2_DA = 997.0, 1127.0
    H1_DA = Q1_DA + Q2_DA
    Q1_CPX, Q2_CPX = 1757.0, 2677.0
    H1_CPX = Q1_CPX + Q2_CPX
    CONS27 = 411877.576070  # Yahoo 0y avg, 53 analysts
    CONS28 = 692094.952520  # +1y avg, 56 analysts
    N_0Y, N_1Y = 53, 56
    STUB_REV = CONS27 - H1_REV  # ~234041.6
    YA_H2 = 57006.0 + 68127.0  # Q3+Q4 FY2026 (Yahoo 2025-10-31 & 2026-01-31)
    STUB_G = STUB_REV / YA_H2 - 1
    G28 = CONS28 / CONS27 - 1
    CASH = 62469.0  # cash + STI @ 2026-07-31
    DEBT = 38351.0  # total debt incl. capital leases @ 2026-07-31
    INV_FA = 51157.0
    # Kd: FY2026 interest 259 / YE debt 11,040 = 2.35%; Q2 debt jumped (8-K 2026-08-17 items 1.01/2.03).
    # EDGAR HTML 403, coupon not read. Incremental debt ≈ 38,351−11,040=27,311 treated at Rf; old book at 2.35%.
    RF = snap["rf"]["value"]
    KD_OLD = 259.0 / 11040.0
    D_OLD, D_NEW = 11040.0, DEBT - 11040.0
    KD = (D_OLD * KD_OLD + D_NEW * RF) / DEBT
    # paths
    # first row stub; then FY2028..FY2036 (9 years) — wait 10 full years after stub? GOOGL does stub + 10 years.
    # Use stub + FY2028–FY2037 (10 years) = 11 rows. Or stub + 10 years ending FY2037.
    n_full = 10
    growth_full = rules.fade_to_g([G28], min(0.03, RF), n_full)
    # margins: H1 op margin 65.95%; FY26 60.38%; 3Y avg FY24-26
    m_fy26 = EBIT26 / REV26
    m_h1 = H1_EBIT / H1_REV
    m_fy25 = 81453.0 / 130497.0
    m_fy24 = 32972.0 / 60922.0
    m_3y = (m_fy26 + m_fy25 + m_fy24) / 3
    stub_m = m_h1  # recent
    margins_full = rules.fade_levels(m_h1, m_3y, n_full)
    da_h1 = H1_DA / H1_REV
    da_fy26 = DA26 / REV26
    da_full = rules.fade_levels(da_h1, da_fy26, n_full)
    cpx_h1 = H1_CPX / H1_REV
    # 4Y capex/sales
    cpx_hist = [6042 / 215938, 3236 / 130497, 1069 / 60922, 1833 / 26974]
    da_hist = [2843 / 215938, 1864 / 130497, 1508 / 60922, 1543 / 26974]
    floor = rules.capex_floor(da_hist, cpx_hist)  # PPE split missing FY25-26; total D&A used, see gap
    cpx_full = rules.fade_levels(cpx_h1, sum(cpx_hist) / 4, n_full)
    # NWC FY26 (AR+Inv-AP)/Rev
    nwc_pct = (38466.0 + 21403.0 - 9812.0) / REV26
    # stub ΔNWC:  nwc_pct * (FY27e - FY26) * (stub/FY27)  approx incremental in remaining half
    dnwc_stub = nwc_pct * (CONS27 - REV26) * (STUB_REV / CONS27)
    # timing
    # remaining mid: Nov 11 2026 to Jan 31 2027 -> mid = 2026-12-21, t=40/365.25
    mid_stub = dt.date(2026, 12, 21)
    t0 = rules.midyear_t(VAL, mid_stub)
    mid_fy28 = dt.date(2027, 7, 31)
    t1 = rules.midyear_t(VAL, mid_fy28)
    discs = [t0] + [t1 + i for i in range(n_full)]
    years = ["FY2027 H2（stub）"] + [f"FY{2028+i}" for i in range(n_full)]
    periods = [0.5] + [1.0] * n_full
    growths = [STUB_G] + growth_full
    margins = [stub_m] + margins_full
    da_pcts = [da_h1] + da_full
    capex_pcts = [H1_CPX and (None)]  # stub capex explicit
    # stub capex: no official FY27 guide; apply H1 capex/sales to stub revenue
    stub_cpx_pct = cpx_h1
    capex_pcts = [stub_cpx_pct] + cpx_full
    # shares floor
    hist_sh = [25070.0, 24940.0, 24804.0, 24514.0, 24285.0]  # diluted WAS FY23-FY26 + Q2 FY27
    shf = rules.shares_floor(SH, hist_sh, years=10, dilution=0.01)
    # consensus growth column: stub vs YA H2; FY28 vs CONS27; later none
    cons_g = [STUB_G, G28] + [None] * (n_full - 1)
    # Rf D()
    rf = D(snap["rf"]["value"], snap["rf"]["basis"], snap["rf"]["source"], snap["rf"]["url"], snap["rf"]["data_date"])
    crp = MA.crp("US")
    U_Y = "https://finance.yahoo.com/quote/NVDA"
    U_YF = "https://finance.yahoo.com/quote/NVDA/financials"
    U_YCF = "https://finance.yahoo.com/quote/NVDA/cash-flow"
    U_YA = "https://finance.yahoo.com/quote/NVDA/analysis/"
    U_TNX = "https://finance.yahoo.com/quote/%5ETNX"
    U_SEC = "https://data.sec.gov/submissions/CIK0001045810.json"
    if snap.get("edgar_html_blocked"):
        gaps.append("SEC EDGAR 網頁檔（10-Q／8-K HTML）回傳 HTTP 403；本次財務數字改用 Yahoo Finance 報表（並以 data.sec.gov submissions 核對最新 10-Q 報告期為 2026-07-26，呈交 2026-08-26）。未讀到 2026-08-17 發債 8-K 票息。")
    gaps.append("Yahoo 未提供 FY2025–FY2026 單獨 PPE 折舊（只有 Reconciled D&A）；9%地板的折舊側用總 D&A／營收，可能略高於純 PPE 折舊。因平均 Capex% 高於平均 D&A%，地板仍由 Capex 決定。")
    gaps.append("Q3 FY2027（約 2026-10 季結）尚未見 10-Q；H2 stub 營收採 Yahoo 本財年一致預期減上半年實際。")
    gaps.append("Yahoo Q2 資產負債表出現 Restricted Cash US$36.9bn，與現金＋短期投資 US$62.5bn 加總不合理，疑為欄位錯置；橋接不加該項。")
    gaps.append("具名投行目標價原文未取得（付費庫／被轉述頁未抓到）；投行表僅列 Yahoo 一致目標價區間。")
    # CFO cross-check FY2026
    cfo = 102718.0
    fcff_cfo = cfo + INT26 * (1 - TAX26) - CPX26
    nopat = EBIT26 * (1 - TAX26)
    # ΔNWC FY26 approx from BS change not computed here in full; leave components
    cross = {
        "year": "FY2026",
        "cfo": cfo,
        "interest": INT26,
        "tax": TAX26,
        "capex": CPX26,
        "fcff_cfo_formula": fcff_cfo,
        "nopat": nopat,
        "da": DA26,
        "note": "US GAAP 利息多在經營現金流；CFO 公式與港股慣例不完全相同。預測仍以 NOPAT 路徑為主。",
        "source": U_YCF,
        "data_date": "2026-01-25",
    }
    first_rev = STUB_REV
    stub_fields = {
        "h1_actual_revenue": H1_REV,
        "fy2026_revenue": CONS27,  # verify_fcff 沿用此欄名＝stub 所屬財年全年營收（此處為 FY2027 一致預期）
        "h1_actual_revenue_note": "欄位名沿用 verify_fcff：此處為 FY2027 上半年（Q1+Q2，截至 2026-07-26）實際營收",
        "ytd_actual_revenue": H1_REV,
    }
    U_YKS = "https://finance.yahoo.com/quote/NVDA/key-statistics"
    growth_notes = (
        [f"Yahoo 本財年營收一致預期 US${CONS27:,.1f}m（{N_0Y} 位）減 H1 實際 US${H1_REV:,.0f}m ＝ H2 stub US${STUB_REV:,.1f}m；"
         f"對比 FY2026 下半年 US${YA_H2:,.0f}m，增長 {STUB_G:.1%}"]
        + [f"Yahoo 下一財年營收一致預期 US${CONS28:,.1f}m（{N_1Y} 位）÷ 本財年一致預期 − 1 ＝ {G28:.1%}"]
        + ["一致預期以外年份：由 FY2028 增長直線淡化至終值 g，規則後備路徑"] * (n_full - 1)
    )
    spec = {
        "asset": "NVIDIA Corporation",
        "ticker": "NVDA",
        "market": "美股",
        "currency": "USD",
        "units": "百萬美元（USD millions）；每股理論價 US$",
        "valuation_date": VAL_DATE,
        "price": P,
        "price_date": PDATE,
        "price_url": U_Y,
        "price_source": "Yahoo Finance",
        "convention": (f"年中折現；估值日 {VAL_DATE}（澳門）。最新資產負債表 2026-07-26／Yahoo 欄 2026-07-31。"
                       f"FY2027 下半年殘段剩餘期間中點約 2026-12-21，t={t0:.3f}；"
                       f"FY2028 年中約 2027-07-31，t={t1:.3f}；其後各年 +1。終值按最後預測年年中 t_N 折現。"),
        "forecast_years_label": "FY2027 H2 stub + FY2028–FY2037（10.5 年）",
        "horizon_rationale": "半導體周期與 AI 加速器需求需完整預測期，讓增長由一致預期的高雙位數淡化至終值 g。",
        "shares_m": SH,
        "shares_source": D(SH, f"市場數據：2026-07-31 已發行 24,147m ＋（Q2 攤薄加權 24,285m − 基本加權 24,190m）＝{SH:.0f}m",
                           "Yahoo Finance quarterly statements", U_YF, "2026-07-31"),
        "debt_m": DEBT,
        "D_basis": f"市場數據：2026-07-31 Yahoo Total Debt US${DEBT:,.0f}m（長期借款 US$32,366m＋流動借款 US$1,000m＋資本租賃 US$4,985m）。經營租賃若未列入 Total Debt，可能低估 D（資料缺口）。",
        "D_source": "Yahoo Finance quarterly balance sheet",
        "D_url": "https://finance.yahoo.com/quote/NVDA/balance-sheet",
        "D_date": "2026-07-31",
        "E_basis": f"市值 = US${P} × {SH:.0f}m 攤薄股（{PDATE} 收市）",
        "rf": rf,
        "crp": crp,
        "kd_pre": D(KD, (f"分析師假設（規則）：FY2026 利息 US$259m ÷ 年末債務 US$11,040m ＝ {KD_OLD:.2%} 用於舊債務；"
                         f"Q2 債務增至 US${DEBT:,.0f}m（約增 US${D_NEW:,.0f}m）。8-K 票息因 EDGAR HTML 403 未讀到，"
                         f"新增債務暫按 Rf {RF:.3%} 計，加權 Kd＝{KD:.2%}。實際票息若更高／更低，WACC 須修訂。"),
                  "Yahoo income/balance；Rf 見折現率", U_YF, "2026-07-31"),
        "tax_rate": TAX,
        "tax_d": D(TAX, f"分析師假設：預測稅率採 Q2 FY2027 Yahoo Tax Rate For Calcs 16.5%（FY2026 有效稅率 {TAX26:.2%}，略低）",
                   "Yahoo Finance quarterly income statement", U_YF, "2026-07-31"),
        "beta_reg": snap.get("beta_reg"),
        "beta_reg_url": U_YKS,
        "beta_reg_date": PDATE,
        "year_labels": years,
        "period_lengths": periods,
        "discount_periods": discs,
        "growths": growths,
        "margins": margins,
        "da_pcts": da_pcts,
        "capex_pcts": capex_pcts,
        "dnwc_pct": nwc_pct,
        "dnwc_rule": f"非現金 NWC≈（應收＋存貨−應付）／營收；FY2026 ＝ {nwc_pct:.1%}；ΔNWC＝該比率×ΔRevenue（殘段按全年增量×殘段佔比）",
        "prev_revenue_for_first": YA_H2,
        "first_revenue": first_rev,
        "first_delta_nwc": dnwc_stub,
        "first_is_stub": True,
        "static_keeps_first_revenue": True,
        "ytd_actual_revenue": H1_REV,
        "full_year_after_first": CONS27,
        "full_year_base_revenue": CONS27,
        "stub_fields": stub_fields,
        "growth_base_notes": growth_notes,
        "growth_rationales": growth_notes,
        "margin_rationales": ([f"上半年營業利潤率 {m_h1:.1%}（GAAP Operating Income）；殘段沿用近期利潤率"]
                              + [f"由近期 {m_h1:.1%} 向 FY2024–26 三年平均 {m_3y:.1%} 淡化"] * n_full),
        "reinvestment_rationales": ([f"殘段 Capex 按上半年 Capex/營收 {stub_cpx_pct:.2%}×殘段營收（公司無公開全年指引）；D&A {da_h1:.2%}"]
                                    + [f"Capex/營收向 4 年均值 {sum(cpx_hist)/4:.2%} 淡化；D&A 向 FY2026 {da_fy26:.2%} 靠攏"] * n_full),
        "row_sources": [U_YA, U_YF, U_YCF, U_SEC],
        "terminal_g": min(0.03, RF),
        "ronic": max(0.20, 0.15),
        "g_rationale": f"分析師假設（規則）：終值 g＝3.0%，不高於美元 Rf {RF:.3%}，約對應美國長期名目增長下沿。",
        "method_rationale": "價值驅動終值：FCFF_{N+1}＝NOPAT_N×(1+g)×(1−g/RONIC)；RONIC＝20%（AI 資本開支周期下的中期投入資本回報假設，非公司指引）。",
        "tv_method": "value_driver",
        "bridge": [
            {"item": "現金、約當現金及短期投資", "value": CASH, "sign": "+",
             "basis": "市場數據：2026-07-31 Yahoo 現金＋短期投資 US$62,469m；全數加回（未扣最低營運現金，略偏樂觀）",
             "source_name": "Yahoo Finance quarterly balance sheet", "url": "https://finance.yahoo.com/quote/NVDA/balance-sheet", "data_date": "2026-07-31"},
            {"item": "金融／長期投資（帳面）", "value": INV_FA, "sign": "+",
             "basis": "市場數據：2026-07-31 Yahoo Investments and Advances／Investment in Financial Assets US$51,157m；按帳面加回",
             "source_name": "Yahoo Finance quarterly balance sheet", "url": "https://finance.yahoo.com/quote/NVDA/balance-sheet", "data_date": "2026-07-31"},
            {"item": "有息債務及資本租賃", "value": DEBT, "sign": "-",
             "basis": "市場數據：2026-07-31 Yahoo Total Debt US$38,351m（含資本租賃）",
             "source_name": "Yahoo Finance quarterly balance sheet", "url": "https://finance.yahoo.com/quote/NVDA/balance-sheet", "data_date": "2026-07-31"},
        ],
        "fx_rate": 1.0,
        "fx_if_any": {"pair": None, "rate": 1.0, "applied": "不適用（美元）", "per_share_fx": 1.0},
        "corrections": [
            {"item": "Damodaran 行業分類", "requested": "按 indname.xls",
             "adopted": "Semiconductor（NVIDIA Corporation, NasdaqGS:NVDA；indname.xls By industry）",
             "reason": "與既有 2330.TW／MU／AVGO 同一行業 βU 多年平均 1.673（betaGlobal.xls 2026-01-05）"},
            {"item": "租賃", "requested": "融資性債務口徑一致",
             "adopted": "Yahoo Total Debt 含 Capital Lease Obligations US$4,985m，已入 WACC 的 D 與橋接；經營租賃是否另列無法從 Yahoo 確認",
             "reason": "避免漏計資本租賃；經營租賃缺口已列入 data_gaps"},
        ],
        "base_year": {
            "year": "FY2026（另附 FY2027 上半年實際）", "year_end": "2026-01-25",
            "currency": "USD millions",
            "revenue": REV26, "ebit": EBIT26, "ebit_margin": m_fy26, "da": DA26, "capex": CPX26,
            "tax_rate_effective": TAX26,
            "h1_fy2027_revenue": H1_REV, "h1_fy2027_ebit": H1_EBIT,
            "bridge_date": "2026-07-31",
            "sources": [U_YF, U_YCF, U_SEC],
        },
        "capex_floor_pct": floor["floor_pct"],
        "capex_floor_detail": {**floor,
                               "years": {"FY2026": {"rev": 215938, "capex": 6042, "da": 2843},
                                         "FY2025": {"rev": 130497, "capex": 3236, "da": 1864},
                                         "FY2024": {"rev": 60922, "capex": 1069, "da": 1508},
                                         "FY2023": {"rev": 26974, "capex": 1833, "da": 1543}}},
        "shares_floor_m": shf["floor_shares_m"],
        "shares_floor_detail": shf,
        "bear_g_delta": -0.03, "bull_g_delta": 0.03,
        "bear_m_delta": -0.02, "bull_m_delta": 0.02,
        "consensus_growths": cons_g,
        "growth_table_notes": [
            "一致預期欄：殘段與 FY2028 來自 Yahoo Analysis 營收估計（取數日見來源）；其後年份 Yahoo 無公開數字，留空。",
            "具名投行增長路徑未取得，不倒推。",
            "悲觀／樂觀：跳過殘段，僅對其後首 4 個完整財年增長 ±3 個百分點、利潤率 ±2 個百分點。",
        ],
        "growth_table_sources": [
            {"name": "Yahoo Finance Analysis NVDA", "url": U_YA, "data_date": PDATE},
        ],
        "ib_compare": snap.get("ib_compare") or {},
        "cfo_fcff_crosscheck": cross,
        "data_gaps": gaps,
        "assumption_source": snap.get("assumption_source") or "rules_fallback",
        "llm_status": snap.get("llm_status") or {},
        "row_sources": [U_YA, U_YF, U_SEC],
    }
    return spec


def spec_1830(snap: dict) -> dict:
    register_industries_from_damodaran()
    gaps = list(snap.get("data_gaps") or [])
    P = snap["price"]
    PDATE = snap["price_date"]
    SH = 1256.197771  # 1,256,197,771
    # official results HK$'000 → HK$m
    REV26, REV25, REV24, REV23 = 962.349, 1127.863, 1393.339, 1389.261
    # Operating profit from official P&L (not Yahoo's slightly different Operating Income)
    OP26, OP25 = 212.986, 251.636
    PPE_DEP26, PPE_DEP25 = 55.466, 67.491
    ROU_DEP26 = 97.767
    DA26 = 153.233  # PPE+ROU (matches Yahoo Reconciled Depreciation)
    CPX26, CPX25, CPX24, CPX23 = 30.258, 34.652, 58.285, 29.047  # Yahoo CF Purchase of PPE
    # note segment capex 24.626 vs CF 30.258
    gaps.append("全年業績分部資本開支合計 HK$24.626m，與 Yahoo／現金流量表購置 PPE HK$30.258m 不一致；模型現金 Capex 採用現金流量表 30.258m，差額列為缺口。")
    TAX = 32842 / 211181  # 0.155516 FY2026 official
    INT26 = 7.949
    # H1 FY2026 (ended 2025-09-30)
    H1_26_REV, H1_26_OP, H1_26_PAT = 485.880, 111.008, 94.804
    H2_26_REV = REV26 - H1_26_REV  # 476.469
    H2_26_OP = OP26 - H1_26_OP  # 101.978
    H2_26_PAT = 83.9  # official highlight (net profit 2H)
    # Profit alert H1 FY2027 PAT 142–147m; midpoint 144.5 vs 94.804 = +52.4%
    PAT_H1_27_LO, PAT_H1_27_HI = 142.0, 147.0
    PAT_H1_27 = (PAT_H1_27_LO + PAT_H1_27_HI) / 2
    # Q1 sales contracts +12.1% (not recognised revenue)
    G_PROXY = 0.121
    gaps.append("2026-11-09 正面盈利預告只披露截至 2026-09-30 止六個月股東應佔溢利約 1.42–1.47 億港元，沒有營收。FY2027 營收增長暫以 2026-07-09 第一季「銷售合約價值」按年 +12.1% 作為唯一量化頂線代理，信心低；中期業績訂於 2026-11-16 公布後必須重跑。")
    FY27_REV = REV26 * (1 + G_PROXY)
    # H1 revenue proxy 12.1%; implied H1 EBIT from PAT
    H1_27_REV = H1_26_REV * (1 + G_PROXY)
    # EBIT ≈ PAT/(1-t) + finance net (H1 finance approx half of 1.805)
    H1_27_EBIT = PAT_H1_27 / (1 - TAX) + (1.805 / 2)
    M_H1_27 = H1_27_EBIT / H1_27_REV
    M_H2_26 = H2_26_OP / H2_26_REV
    H2_27_REV = H2_26_REV * (1 + G_PROXY)
    H2_27_EBIT = M_H2_26 * H2_27_REV  # conservative: H2 margin last year, not annualised H1 spike
    FY27_EBIT = H1_27_EBIT + H2_27_EBIT
    M27 = FY27_EBIT / FY27_REV
    # mid-cycle operating margins FY23-26 official/yahoo
    m26 = OP26 / REV26
    m25 = OP25 / REV25
    m24 = 381.351 / REV24
    m23 = 394.538 / REV23
    m_avg = (m26 + m25 + m24 + m23) / 4
    n = 10  # FY2027–FY2036
    RF = snap["rf"]["value"]
    G_TERM = min(0.025, RF)
    growths = rules.fade_to_g([G_PROXY], G_TERM, n)
    margins = [M27] + rules.fade_levels(M27, m_avg, n)[1:]
    da_pct26 = DA26 / REV26
    da_pcts = [da_pct26] * n
    cpx_hist = [CPX26 / REV26, CPX25 / REV25, CPX24 / REV24, CPX23 / REV23]
    ppe_hist = [PPE_DEP26 / REV26, PPE_DEP25 / REV25]
    floor = rules.capex_floor(ppe_hist, cpx_hist)
    gaps.append(f"PPE 折舊僅有業績公告 FY2025–FY2026 兩年可分開（{PPE_DEP25}／{PPE_DEP26} 百萬港元）；FY2023–FY2024 Yahoo 只有總 D&A。地板以 2 年 PPE 折舊均值與 4 年現金 Capex 均值取較高。")
    cpx_pcts = [sum(cpx_hist) / 4] * n
    # NWC including deferred revenue (customer prepayments)
    # FY26: AR 30.156 + inv 5.238 + other rec 2.092 - AP 2.360 - other payable 15.921 - def rev 193.687 - 2.691
    # Other payable/tax: tax payable excluded. Other rec/pay from Yahoo.
    # Use AR+Inv-AP-deferred revenue / rev as simpler official-enough set
    nwc26 = 30.156 + 5.238 - 2.360 - 193.687 - 2.691
    nwc_pct = nwc26 / REV26  # largely negative due to deferred revenue
    gaps.append("非現金 NWC 含遞延收入（預付療程），FY2026 為大幅負值。增長時 ΔNWC 可能為負（釋放現金）；若中期後遞延收入結構改變，此項須修訂。未計其他應付／預付款的完整明細。")
    # timing: FY ends 31 Mar. FY2027 mid 2026-09-30 already past → t=0
    # FY2028 mid 2027-09-30
    t0 = 0.0
    t1 = rules.midyear_t(VAL, dt.date(2027, 9, 30))
    discs = [t0] + [t1 + i for i in range(n - 1)]
    years = [f"FY{2027+i}" for i in range(n)]
    periods = [1.0] * n
    shf = rules.shares_floor(SH, [1256.536, 1256.197771, 1256.197771, 1256.197], years=10, dilution=0.01)
    # Kd actual lease interest 7.949 / 134.584
    DEBT = 134.584  # all leases
    KD = 7.949 / DEBT
    CASH = 289.337 + 5.597
    INV_FA = 228.406
    NCI = -1.681
    DTL = 24.677
    U_Y = "https://finance.yahoo.com/quote/1830.HK"
    U_ALERT = "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/1109/2026110900781.pdf"
    U_AR = "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0715/2026071500340.pdf"
    U_FR = "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0630/2026063002439.pdf"
    U_IR = "https://ir.perfectmedical.com/c/ir_announcement.php"
    U_OPS = "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0709/2026070900137.pdf"
    U_INT = "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/1128/2025112802112.pdf"
    U_BRD = "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/1104/2026110401654.pdf"
    U_ADB = snap["rf"]["url"]
    # geo FY2026: HK 79.6% → CRP HK
    crp = MA.crp("HK", "（FY2026 全年業績：香港收入 HK$765.591m，佔 79.6%；境外（中國內地、澳門、澳洲、新加坡）20.4%。風險來源以香港為主，故 CRP 用香港而非中國。境外仍有內地業務，屬次要。）")
    rf = D(snap["rf"]["value"], snap["rf"]["basis"], snap["rf"]["source"], snap["rf"]["url"], snap["rf"]["data_date"])
    # last FY growth was -14.7%; we do not use it for FY27 given contracts + profit alert
    cfo = 339.477
    fcff_cfo = cfo + INT26 * (1 - TAX) - CPX26
    nopat = OP26 * (1 - TAX)
    # rough ΔNWC not fully reconstructed; report formula values
    cross = {
        "year": "FY2026",
        "cfo": cfo, "interest": INT26, "tax": TAX, "capex": CPX26,
        "fcff_cfo_formula": fcff_cfo,
        "fcff_cfo_note": "FCFF≈CFO+利息×(1−t)−Capex（利息在融資現金流 Interest Paid Cff）",
        "nopat": nopat, "da": DA26,
        "note": "NOPAT 路徑另減 ΔNWC；與 CFO 公式差額主要來自營運資金（含遞延收入）及非現金項目。預測以 NOPAT 為主。",
        "source": U_FR, "data_date": "2026-03-31",
    }
    gaps.append("Yahoo 及披露易均無分析師營收／目標價共識（覆蓋為零）；投行比較表因此只有空白具名列，不填假目標價。")
    gaps.append("最新完整資產負債表仍為 2026-03-31；H1 FY2027 的現金、債務、派息、遞延收入尚未公布（董事會 2026-11-16 審批中期業績）。橋接日期早於估值日約 7.5 個月。")
    gaps.append("2026-11-10 收市 HK$1.49，較 11/09 收市 HK$1.20 大升且成交放大，時點在 11/09 盈利預告之後；模型用 11/10 收市，不把短期漲幅當成基本面已完全反映或未反映的結論。")
    growth_notes = [f"規則後備：無 Yahoo 營收共識。FY2027 採用 2026-07-09 公告第一季銷售合約價值按年 +{G_PROXY:.1%} 作為全年代理；其後直線淡化至終值 g {G_TERM:.1%}"] + ["向終值增長直線淡化"] * (n - 1)
    spec = {
        "asset": "完美醫療健康管理有限公司 Perfect Medical Health Management Limited",
        "ticker": "1830.HK",
        "market": "香港",
        "currency": "HKD",
        "units": "百萬港元（HK$ million）；每股理論價 HK$",
        "valuation_date": VAL_DATE,
        "price": P, "price_date": PDATE, "price_url": U_Y, "price_source": "Yahoo Finance",
        "convention": (f"年中折現；估值日 {VAL_DATE}（澳門）。年結 3 月 31 日。最新已入帳資產負債表 2026-03-31，"
                       f"故預測自 FY2027 全年起（H1 已過但未入帳）。FY2027 年中 2026-09-30 已過去，t＝0；"
                       f"FY2028 年中 2027-09-30，t={t1:.3f}；其後各年 +1。模型貨幣港元。"),
        "forecast_years_label": "FY2027–FY2036（10 年）",
        "horizon_rationale": "醫療美容及健康管理網絡調整與利潤率修復需要完整周期，再收斂至終值增長。",
        "shares_m": SH,
        "shares_source": D(SH, "市場數據：2026-03-31 已發行普通股 1,256,197,771 股；Yahoo／年結攤薄加權均為 1,256.197m，四年大致持平",
                           "全年業績公告／Yahoo", U_FR, "2026-03-31"),
        "debt_m": DEBT,
        "D_basis": f"市場數據：2026-03-31 計息債務＝租賃負債 HK${DEBT:.3f}m（Yahoo Total Debt 與 Capital Lease Obligations 相同，無銀行借款）",
        "D_source": "全年業績公告／Yahoo balance sheet", "D_url": U_FR, "D_date": "2026-03-31",
        "E_basis": f"市值 = HK${P} × {SH:.6f}m 股（{PDATE} 收市）",
        "rf": rf, "crp": crp,
        "kd_pre": D(KD, f"市場數據：FY2026 融資成本（租賃利息）HK$7.949m ÷ 年末租賃負債 HK$134.584m ＝ {KD:.2%}（與現金流量表 Payment of interest element of lease liabilities 一致）",
                    "全年業績公告", U_FR, "2026-03-31"),
        "tax_rate": TAX,
        "tax_d": D(TAX, f"分析師假設：預測稅率採 FY2026 有效稅率 {TAX:.2%}（稅項 HK$32.842m ÷ 除稅前 HK$211.181m）。香港利得稅法定 16.5%，集團另有內地 25%、澳門、澳洲、新加坡稅率。",
                   "全年業績公告", U_FR, "2026-03-31"),
        "beta_reg": snap.get("beta_reg"),
        "beta_reg_url": "https://finance.yahoo.com/quote/1830.HK/key-statistics",
        "beta_reg_date": PDATE,
        "year_labels": years, "period_lengths": periods, "discount_periods": discs,
        "growths": growths, "margins": margins, "da_pcts": da_pcts, "capex_pcts": cpx_pcts,
        "dnwc_pct": nwc_pct,
        "dnwc_rule": f"ΔNWC＝{nwc_pct:.1%}×ΔRevenue（FY2026 應收＋存貨−應付−遞延收入）／營收",
        "prev_revenue_for_first": REV26,
        "first_revenue": FY27_REV,
        "first_is_stub": False,
        "static_keeps_first_revenue": False,
        "static_base_revenue": REV26,
        "growth_base_notes": growth_notes,
        "growth_rationales": growth_notes,
        "margin_rationales": [f"FY2027：H1 按盈利預告中位溢利 HK${PAT_H1_27:.1f}m 還原 EBIT 利潤率 {M_H1_27:.1%}（營收用 +12.1% 代理）；H2 利潤率沿用 FY2026 下半年 {M_H2_26:.1%}，不把上半年升幅年化。全年加權 {M27:.1%}。其後向四年平均營業利潤率 {m_avg:.1%} 淡化"]
        + [f"向 FY2023–26 平均營業利潤率 {m_avg:.1%} 淡化"] * (n - 1),
        "reinvestment_rationales": [f"總 D&A（含使用權折舊）沿用 FY2026 {da_pct26:.1%} 營收；現金 PPE Capex 用四年均值 {sum(cpx_hist)/4:.2%}；租賃本金／利息視為融資"] * n,
        "row_sources": [U_FR, U_ALERT, U_OPS, U_Y],
        "terminal_g": G_TERM, "ronic": 0.12,
        "g_rationale": f"終值 g＝2.5%，不高於港元 Rf {RF:.3%}；醫療美容長期名目增長下沿的規則假設。",
        "method_rationale": "價值驅動終值：RONIC＝12%（接近或略高於服務業中周期投入資本回報的規則假設，非公司指引）。",
        "tv_method": "value_driver",
        "bridge": [
            {"item": "現金、短期投資及受限制現金", "value": CASH, "sign": "+",
             "basis": "市場數據：2026-03-31 現金及短期投資 HK$289.337m＋受限制現金 HK$5.597m；全數加回（未扣最低營運現金）",
             "source_name": "全年業績公告／Yahoo", "url": U_FR, "data_date": "2026-03-31"},
            {"item": "金融資產投資（帳面）", "value": INV_FA, "sign": "+",
             "basis": "市場數據：2026-03-31 Investment in Financial Assets HK$228.406m；按帳面（FVTOCI 波動已見儲備，未另作評估）",
             "source_name": "全年業績公告／Yahoo", "url": U_FR, "data_date": "2026-03-31"},
            {"item": "租賃負債（HKFRS 16）", "value": DEBT, "sign": "-",
             "basis": "市場數據：2026-03-31 租賃負債 HK$134.584m；EBIT 已扣使用權折舊，負債入橋接與 WACC 的 D",
             "source_name": "全年業績公告／Yahoo", "url": U_FR, "data_date": "2026-03-31"},
            {"item": "遞延稅項負債", "value": DTL, "sign": "-",
             "basis": "市場數據：2026-03-31 非流動遞延稅負債 HK$24.677m",
             "source_name": "全年業績公告／Yahoo", "url": U_FR, "data_date": "2026-03-31"},
            {"item": "非控股權益", "value": abs(NCI), "sign": "+" if NCI < 0 else "-",
             "basis": f"市場數據：2026-03-31 非控股權益 HK${NCI:.3f}m（負數，少數股東權益為負；加回 {abs(NCI):.3f}m）",
             "source_name": "全年業績公告／Yahoo", "url": U_FR, "data_date": "2026-03-31"},
        ],
        "fx_rate": 1.0,
        "fx_if_any": {"pair": None, "rate": 1.0, "applied": "不適用（港元）", "per_share_fx": 1.0, "cnyhkd": snap.get("cnyhkd")},
        "corrections": [
            {"item": "Damodaran 行業分類", "requested": "按 indname.xls 不得臆測",
             "adopted": "Business & Consumer Services（Perfect Medical Health Management Limited, SEHK:1830；SIC 7200；國家 Hong Kong）",
             "reason": "不是 Hospitals/Healthcare Facilities。βU 多年平均 0.9617623654591464（betaGlobal.xls 2026-01-05 Average 2020-24）"},
            {"item": "HKFRS 16 租賃", "requested": "PPE capex 與總 D&A 分開",
             "adopted": "加回總 D&A（含使用權折舊）；現金 Capex 只用購置 PPE；租賃負債入 D 與橋接",
             "reason": "與手冊港股專章及 1883／9896 處理一致"},
            {"item": "CRP", "requested": "按風險來源選香港或中國",
             "adopted": "香港（FY2026 收入 79.6% 來自香港）",
             "reason": "境外 20.4% 含內地，但不是主要收入來源"},
        ],
        "base_year": {
            "year": "FY2026", "year_end": "2026-03-31",
            "currency": "HKD millions",
            "revenue": REV26, "ebit": OP26, "ebit_margin": m26, "da": DA26, "ppe_depreciation": PPE_DEP26,
            "capex": CPX26, "tax_rate_effective": TAX,
            "h1_fy2026_revenue": H1_26_REV, "profit_alert_h1_fy2027_pat_mid": PAT_H1_27,
            "geo_fy2026": {"Hong Kong": 765.591, "Outside Hong Kong": 196.758},
            "bridge_date": "2026-03-31",
            "sources": [U_FR, U_AR, U_ALERT, U_OPS],
        },
        "capex_floor_pct": floor["floor_pct"],
        "capex_floor_detail": {**floor, "ppe_dep_fy26": PPE_DEP26, "ppe_dep_fy25": PPE_DEP25,
                               "capex_cf": {"FY2026": CPX26, "FY2025": CPX25, "FY2024": CPX24, "FY2023": CPX23}},
        "shares_floor_m": shf["floor_shares_m"],
        "shares_floor_detail": shf,
        "bear_g_delta": -0.03, "bull_g_delta": 0.03,
        "bear_m_delta": -0.02, "bull_m_delta": 0.02,
        "consensus_growths": [None] * n,
        "growth_table_notes": [
            "Yahoo 無營收一致預期，一致預期欄留空。",
            "FY2027 基準增長 12.1% 來自第一季銷售合約價值，不是已確認營收，也不是盈利預告中的溢利增幅（+50–55% 只適用於 H1 溢利）。",
            "具名投行增長路徑未取得。",
        ],
        "growth_table_sources": [
            {"name": "HKEX 未經審核營運數據（截至 2026-06-30 止三個月）", "url": U_OPS, "data_date": "2026-07-09"},
            {"name": "HKEX 正面盈利預告", "url": U_ALERT, "data_date": "2026-11-09"},
        ],
        "ib_compare": snap.get("ib_compare") or {},
        "cfo_fcff_crosscheck": cross,
        "data_gaps": gaps,
        "assumption_source": snap.get("assumption_source") or "rules_fallback",
        "llm_status": snap.get("llm_status") or {},
        "filing_urls": {"profit_alert": U_ALERT, "final_results": U_FR, "annual_report": U_AR,
                        "ops_update": U_OPS, "board_meeting": U_BRD, "ir": U_IR, "interim_prior": U_INT},
    }
    return spec
