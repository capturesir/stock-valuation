#!/usr/bin/env python3
"""Per-asset FCFF DCF (DCFF) calculation sheets with LIVE Excel formulas, built from models/calc/<ticker>_latest.json.

add_calc_sheet(wb, d, name=None, peer=None, lang="zh-Hant") writes:
  A. Discount rate inputs (WACC)
  B. Base-year figures and rules
  C. Annual projections with live Excel formulas
  D. Terminal value & Enterprise Value
  E. Equity bridge
  F. Per-share fair value + JSON cross-check
  G. Live WACC x g sensitivity matrix + JSON comparison
  H. Scenarios, revision log, and spec deviations
Returns a dict of key cell addresses (fair value, etc.).
"""
import re
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter as CL

from .i18n import DEFAULT, font_name, kind_label, normalize_lang, scenario_name, t as tr

FN = "Microsoft JhengHei"
T_F = Font(name=FN, bold=True, size=14, color="1F3A5F")
SEC_F = Font(name=FN, bold=True, size=11.5, color="FFFFFF")
SEC_FILL = PatternFill("solid", fgColor="1F3A5F")
HDR_F = Font(name=FN, bold=True, size=10, color="1F3A5F")
HDR_FILL = PatternFill("solid", fgColor="DCE5F0")
IN_F = Font(name=FN, size=10, color="1F5FAF")            # input (from JSON / source)
IN_FILL = PatternFill("solid", fgColor="FFF9E6")
CALC_F = Font(name=FN, size=10, color="000000")          # formula
KEY_F = Font(name=FN, size=11, bold=True, color="000000")
KEY_FILL = PatternFill("solid", fgColor="E2EFDA")
TXT_F = Font(name=FN, size=9.5, color="404650")
NOTE_F = Font(name=FN, size=9.5, italic=True, color="6B7280")
LINK_F = Font(name=FN, size=9.5, color="1F5FAF", underline="single")
thin = Side(style="thin", color="C8D0DA")
BD = Border(left=thin, right=thin, top=thin, bottom=thin)
PCT, PCT3, NUM0, NUM2, NUM4, MULT = "0.00%", "0.000%", "#,##0;-#,##0", "#,##0.00;-#,##0.00", "0.0000", "0.00\"x\""
GREEN = (PatternFill("solid", fgColor="C6EFCE"), Font(name=FN, color="006100", bold=True))
RED = (PatternFill("solid", fgColor="FFC7CE"), Font(name=FN, color="9C0006", bold=True))
SYM = {"TWD": "NT$", "HKD": "HK$", "USD": "US$", "JPY": "¥", "KRW": "₩", "CNY": "RMB"}   # JPY/KRW/CNY added 2026-10-08
WIDTHS = {"A": 34, "B": 15}


def trade_currency(d: dict) -> str:
    """Currency of the share price and the per-share fair value.

    Different from ``d["currency"]`` (the model currency) when the statements are
    reported in another currency, e.g. CNOOC reports in CNY but trades in HKD.
    """
    pair = ((d.get("fx_if_any") or {}).get("pair") or "")
    if "/" in pair:
        return pair.split("/")[1]
    return d.get("currency") or ""


def as_source_text(v):
    """shares_source may be a plain str or an audit dict {basis, source_name, ...} (e.g. 0178.HK / 0941.HK)."""
    if v is None:
        return ""
    if isinstance(v, dict):
        return str(v.get("basis") or v.get("source_name") or v.get("value") or "")
    return str(v)


def resolve_nwc_rate(d: dict) -> float:
    """Official ΔNWC / ΔRevenue rate used by the engine, for the live Excel input cell.

    Prefer base_year["nwc_rate"] when present. Otherwise recover it from full-year
    projection rows (skip stub periods), where ΔNWC = rate × ΔRevenue. Do not fall
    back to a silent 5% template default: that was the 2026-11-11 export bug
    (NVDA / 1830.HK formula prices diverged from the official JSON fair value).
    """
    by = d.get("base_year") or {}
    if by.get("nwc_rate") is not None:
        return float(by["nwc_rate"])
    projs = d.get("projections") or []
    rates = []
    for i in range(1, len(projs)):
        prev, cur = projs[i - 1], projs[i]
        plen_prev = float(prev.get("period_length_years") or 0)
        plen_cur = float(cur.get("period_length_years") or 0)
        if plen_prev < 0.95 or plen_cur < 0.95:
            continue
        drev = cur["revenue"] - prev["revenue"]
        if abs(drev) < 1e-6:
            continue
        rates.append(cur["delta_nwc"] / drev)
    if not rates and projs:
        p0 = projs[0]
        if float(p0.get("period_length_years") or 0) >= 0.95 and by.get("revenue") is not None:
            drev = p0["revenue"] - by["revenue"]
            if abs(drev) > 1e-6:
                rates.append(p0["delta_nwc"] / drev)
    if not rates:
        ticker = d.get("ticker") or "?"
        raise ValueError(
            f"無法從 JSON 還原 {ticker} 的 ΔNWC 比率；請在 base_year.nwc_rate 寫入引擎實際比率，不可再用 5% 預設。"
        )
    rates.sort()
    return rates[len(rates) // 2]


def kind_of(basis):
    b = basis or ""
    if b.startswith("市場數據") and "分析師假設" in b:
        return "市場數據＋分析師假設"
    for k in ("市場數據", "分析師假設", "計算"):
        if b.startswith(k):
            return k
    return ""


def disp(t):
    return t.rsplit(".", 1)[0] if re.fullmatch(r"\d{4}\.(TW|HK)", t) else t


class W:
    def __init__(self, ws, lang: str = DEFAULT):
        self.ws, self.r, self.ref = ws, 1, {}
        self.lang = normalize_lang(lang)
        self.fn = font_name(self.lang)
        self.t_f = Font(name=self.fn, bold=True, size=14, color="1F3A5F")
        self.sec_f = Font(name=self.fn, bold=True, size=11.5, color="FFFFFF")
        self.hdr_f = Font(name=self.fn, bold=True, size=10, color="1F3A5F")
        self.in_f = Font(name=self.fn, size=10, color="1F5FAF")
        self.calc_f = Font(name=self.fn, size=10, color="000000")
        self.key_f = Font(name=self.fn, size=11, bold=True, color="000000")
        self.txt_f = Font(name=self.fn, size=9.5, color="404650")
        self.note_f = Font(name=self.fn, size=9.5, italic=True, color="6B7280")
        self.link_f = Font(name=self.fn, size=9.5, color="1F5FAF", underline="single")
        self.bold_f = Font(name=self.fn, size=10, bold=True)
        self.lbl_f = Font(name=self.fn, size=10, bold=True, color="262B33")
        self.green_f = Font(name=self.fn, color="006100", bold=True)
        self.red_f = Font(name=self.fn, color="9C0006", bold=True)

    def cell(self, r, c, v, font=None, fmt=None, fill=None, wrap=False, align=None, border=True):
        x = self.ws.cell(r, c, v)
        x.font = font or self.calc_f
        if fmt: x.number_format = fmt
        if fill: x.fill = fill
        if border: x.border = BD
        x.alignment = Alignment(vertical="top", wrap_text=wrap, horizontal=align)
        return x

    def text_span(self, r, c1, c2, v, font=None, link=False):
        if c2 > c1:
            self.ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
        x = self.cell(r, c1, v if v not in ("", None) else None, self.link_f if (link and v) else (font or self.txt_f), wrap=True)
        if link and v and str(v).startswith("http"):
            x.hyperlink = str(v)
        for c in range(c1 + 1, c2 + 1):
            self.ws.cell(r, c).border = BD
        return x

    def height(self, r, texts_widths, base=15):
        n = 1
        for t, w in texts_widths:
            if t:
                L = sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(t))
                n = max(n, -(-L // max(1, int(w))))
        self.ws.row_dimensions[r].height = min(300, max(base, 13.5 * n + 3))

    def section(self, title, ncol=18):
        self.r += 1
        for c in range(1, ncol + 1):
            self.ws.cell(self.r, c).fill = SEC_FILL
        x = self.ws.cell(self.r, 1, title); x.font = self.sec_f
        self.ws.row_dimensions[self.r].height = 20
        self.r += 1

    def header(self, labels, start=1, spans=None):
        spans = spans or {}
        c = start
        for lab in labels:
            c2 = spans.get(lab, c)
            if c2 > c:
                self.ws.merge_cells(start_row=self.r, start_column=c, end_row=self.r, end_column=c2)
            self.cell(self.r, c, lab, self.hdr_f, fill=HDR_FILL, wrap=True, align="center")
            for k in range(c + 1, c2 + 1):
                self.ws.cell(self.r, k).border = BD; self.ws.cell(self.r, k).fill = HDR_FILL
            c = c2 + 1
        self.ws.row_dimensions[self.r].height = 30
        self.r += 1

    # input-block row: A label, B value/formula, C unit, D kind, E:K basis, L:N source, O:Q url, R date
    def inp(self, key, label, value, fmt=None, unit="", basis="", src="", url="", date="", kind=None, key_row=False):
        r = self.r
        is_f = isinstance(value, str) and value.startswith("=")
        self.cell(r, 1, label, self.key_f if key_row else self.lbl_f, wrap=True)
        is_txt = isinstance(value, str) and not is_f
        self.cell(r, 2, value, self.key_f if key_row else (self.calc_f if is_f else self.in_f), fmt,
                  KEY_FILL if key_row else (None if is_f else IN_FILL), align="left" if is_txt else "right", wrap=is_txt)
        self.cell(r, 3, unit or None, self.txt_f, align="center")
        raw_k = kind if kind is not None else (kind_of(basis) or ("計算" if is_f else ""))
        disp_k = kind_label(raw_k, self.lang)
        color = {"市場數據": "1F5FAF", "分析師假設": "9C5700", "計算": "4B5563"}.get(raw_k, "6B4FA0")
        self.cell(r, 4, disp_k or None, Font(name=self.fn, size=9.5, bold=True, color=color), align="center", wrap=True)
        self.text_span(r, 5, 11, basis)
        self.text_span(r, 12, 14, src)
        self.text_span(r, 15, 17, url, link=True)
        self.cell(r, 18, date or None, self.txt_f, align="center")
        self.height(r, [(basis, 7 * 12 - 2), (src, 3 * 12 - 2), (label, 32), (value if isinstance(value, str) and not str(value).startswith("=") else "", 14)])
        self.ref[key] = f"$B${r}"
        self.r += 1
        return f"$B${r}"


def _pick_debt(d):
    items = [b for b in d["bridge"] if b["sign"] == "-" and re.search(r"借款|租賃|長期債務", b["item"])]
    target = d["capital_structure"]["D_model_ccy_m"]
    if abs(sum(b["value"] for b in items) - target) < 1e-6 * max(1, target):
        return items
    return None


def add_calc_sheet(wb, d, name=None, peer=None, lang: str = DEFAULT):
    """d: calc JSON dict. peer: (sheet_name, refs) of the 2330.TW sheet for the TSM cross-check."""
    lang = normalize_lang(lang)
    if name is None:
        name = tr("sheet.calc", lang)
    ws = wb.create_sheet(name)
    w = W(ws, lang=lang)
    t, ccy = d["ticker"], d["currency"]
    # share price and per-share values are in the trading currency, which may
    # differ from the model currency used by the FCFF rows (see trade_currency)
    sym = SYM.get(trade_currency(d), trade_currency(d))
    MU = {"USD": "US$m", "JPY": "¥m", "KRW": "₩m"}.get(d.get("model_ccy", "USD"), "US$m")   # model-currency unit
    disc, fx = d["discount"], d["fx_if_any"]
    _dr0 = d["projections"][0].get("drivers") or {}
    memory = "dram_revenue" in _dr0          # MU: DRAM / NAND bit x ASP drivers
    # CNOOC-only unit-economics path; other JSONs may carry drivers without calibration (e.g. 1883.HK)
    cnooc = ("unit_economics_calibration" in d) and bool(_dr0) and not memory
    is_adr = bool(d.get("adr_ratio"))
    for col in range(1, 30):
        ws.column_dimensions[CL(col)].width = 12.5
    for k, v in WIDTHS.items():
        ws.column_dimensions[k].width = v

    std_hdr = [
        tr("calc.hdr.item", lang), tr("calc.hdr.value", lang), tr("calc.hdr.unit", lang),
        tr("calc.hdr.kind", lang), tr("calc.hdr.basis", lang), tr("calc.hdr.source", lang),
        tr("calc.hdr.url", lang), tr("calc.hdr.date", lang),
    ]
    std_spans = {tr("calc.hdr.basis", lang): 11, tr("calc.hdr.source", lang): 14, tr("calc.hdr.url", lang): 17}

    # ---------------------------------------------------------------- title
    x = ws.cell(1, 1, tr("calc.title", lang, asset=d["asset"], ticker=disp(t))); x.font = w.t_f
    ws.row_dimensions[1].height = 24
    notes = [
        tr("calc.note.units", lang, units=d["units"], val_date=d["valuation_date"], price_date=d["price_date"]),
        tr("calc.note.convention", lang, convention=d["discounting_convention"]),
        tr("calc.note.legend", lang),
    ]
    for i, n in enumerate(notes, 2):
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=18)
        c = ws.cell(i, 1, n); c.font = w.note_f; c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[i].height = 15 if len(n) < 150 else 28
    w.r = len(notes) + 1

    # ---------------------------------------------------------------- A. inputs
    w.section(tr("calc.sec.a", lang))
    w.header(std_hdr, spans=std_spans)

    def D_(key, label, fmt=PCT3, unit="", formula=None, key_row=False):
        it = disc[key]
        w.inp(key, label, formula or it["value"], fmt, unit, it.get("basis", ""), it.get("source_name", ""),
              it.get("url", ""), it.get("data_date", ""), key_row=key_row)

    D_("rf", tr("calc.a.rf", lang))
    D_("erp", tr("calc.a.erp", lang))
    D_("crp", tr("calc.a.crp", lang))
    D_("beta_ref", tr("calc.a.beta_ref", lang), NUM4)
    D_("beta_adopted", tr("calc.a.beta_adopted", lang), NUM4)
    R = w.ref
    D_("ke", tr("calc.a.ke", lang), formula=f"={R['rf']}+{R['beta_adopted']}*({R['erp']}+{R['crp']})")
    D_("kd_pre_tax", tr("calc.a.kd_pre_tax", lang))
    D_("tax_rate", tr("calc.a.tax_rate", lang))
    D_("kd_after_tax", tr("calc.a.kd_after_tax", lang), formula=f"={R['kd_pre_tax']}*(1-{R['tax_rate']})")
    # E inputs
    cs = d["capital_structure"]
    if t == "TSM" and peer:
        e_px, e_sh, e_fx = peer["json"]["price"], peer["json"]["shares"], peer["json"]["fx_if_any"]["per_share_fx"]
        e_note = "TSM 與 2330.TW 共用同一 WACC：E 用台股市值（主要掛牌地）"
        e_sym = "NT$"
    else:
        e_px, e_sh = d["price"], d["shares"]
        e_fx = d["fx_if_any"].get("per_share_fx", 1.0) or 1.0
        e_note, e_sym = "", sym
    u_msh = tr("calc.unit.m_shares", lang)
    w.inp("e_px", tr("calc.a.e_px", lang, sym=e_sym), e_px, NUM2, e_sym, f"市場數據：{d['price_date']} 收市" + ("；" + e_note if e_note else ""),
          "", d["price_source_url"] if not e_note else peer["json"]["price_source_url"], d["price_date"])
    w.inp("e_sh", tr("calc.a.e_sh", lang), e_sh, NUM2, u_msh, "市場數據：" + as_source_text(peer["json"]["shares_source"] if e_note else d["shares_source"]))
    w.inp("e_fx", tr("calc.a.e_fx", lang), e_fx, NUM4, "", "市場數據：" + (fx.get("pair") or "不適用（美元）") +
          (f" {d['price_date']} 收市" if e_fx != 1 else ""), fx.get("source_name", ""), fx.get("url", ""), fx.get("data_date", ""))
    ia = d.get("inputs_audit") or {}
    if ia.get("pref_shares") and ia.get("pref_px") and not e_note:
        # preferred shares (e.g. 009150.KS): counted 1:1 in the per-share denominator, but valued at their own price in E
        w.inp("pref_sh", tr("calc.a.pref_sh", lang), ia["pref_shares"], NUM2, u_msh, f"市場數據：資產負債表優先股數（{ia.get('pref_sym', '')}）")
        w.inp("pref_px", tr("calc.a.pref_px", lang, sym=e_sym), ia["pref_px"], NUM2, e_sym, f"市場數據：{ia.get('pref_sym', '')} {d['price_date']} 收市", "Yahoo Finance chart API",
              f"https://finance.yahoo.com/quote/{ia.get('pref_sym', '')}/history/", d["price_date"])
        w.inp("E", tr("calc.a.e_pref", lang), f"=({R['e_px']}*({R['e_sh']}-{R['pref_sh']})+{R['pref_px']}*{R['pref_sh']})/{R['e_fx']}", NUM0, MU,
              disc["weight_e"]["basis"], kind="計算")
    else:
        w.inp("E", tr("calc.a.e", lang), f"={R['e_px']}*{R['e_sh']}/{R['e_fx']}", NUM0, MU,
              disc["weight_e"]["basis"], kind="計算")
    w.r_debt = w.r
    w.ref["D"] = f"$B${w.r}"; w.r += 1   # filled after the bridge block (references bridge cells)
    w.inp("we", tr("calc.a.we", lang), f"={R['E']}/({R['E']}+{R['D']})", PCT3, kind="計算")
    w.inp("wd", tr("calc.a.wd", lang), f"={R['D']}/({R['E']}+{R['D']})", PCT3, kind="計算")
    w.inp("wacc", tr("calc.a.wacc", lang), f"={R['we']}*{R['ke']}+{R['wd']}*{R['kd_after_tax']}", PCT3,
          basis=disc["wacc"]["basis"], key_row=True)
    term = d["terminal"]
    w.inp("g", tr("calc.a.g", lang), term["g"], PCT, basis=term.get("g_rationale", ""))
    if term["method"] == "value_driver":
        w.inp("ronic", tr("calc.a.ronic", lang), term["ronic"], PCT, basis=term.get("method_rationale", ""))
    else:
        w.inp("tvm", tr("calc.a.tvm", lang), "FCFF_N ×（1+g）", None, basis=term.get("method_rationale", ""))
    w.inp("horizon", tr("calc.a.horizon", lang), d["forecast_years"], None, basis="分析師假設：" + d.get("forecast_horizon_rationale", ""))

    # ---------------------------------------------------------------- B. base & rules
    by = d["base_year"]; p0 = d["projections"][0]
    w.section(tr("calc.sec.b", lang))
    w.header(std_hdr, spans=std_spans)
    if memory:
        rs = by["revenue_source"]; sp = by["fy2026_split_used"]
        w.inp("rev_fy26", "FY2026 營收（實際）", by["revenue"], NUM0, MU, rs.get("basis", ""), rs.get("source_name", ""), rs.get("url", ""), rs.get("data_date", ""))
        w.inp("dram26", "FY2026 DRAM 營收（取下限）", sp["dram"], NUM0, MU, "分析師假設：" + (sp.get("note") or "管理層稱 FY2026 DRAM 營收超過 US$100bn，取下限"), "Micron FQ4-26 法說會逐字稿", "https://www.tradingkey.com/news/transcripts/262195079-tradingkey", "2026-09-30")
        w.inp("nand26", "FY2026 NAND＋其他營收 = 總營收 − DRAM", f"={w.ref['rev_fy26']}-{w.ref['dram26']}", NUM0, MU, "計算", kind="計算")
        w.inp("nwc_rate", tr("calc.b.nwc_rate", lang), 0.15, PCT, "", "分析師假設：" + p0["delta_nwc_rule"] + "；" + (by.get("nwc_note") or ""))
    elif not cnooc:
        rs = by.get("revenue_source", {}); h1 = by.get("h1_2026", {}); h1s = h1.get("source", {})
        lb = by.get("labels") or {}          # coverage-expansion JSONs carry their own period labels
        w.inp("rev_fy25", lb.get("rev_base", "FY2025 營收"), by["revenue"], NUM0, MU, rs.get("basis", ""), rs.get("source_name", ""), rs.get("url", ""), rs.get("data_date", ""))
        w.inp("rev_h1", lb.get("rev_ytd", "1H2026 實際營收"), p0["h1_actual_revenue"], NUM0, MU, h1s.get("basis", "市場數據"), h1s.get("source_name", ""), h1s.get("url", ""), h1s.get("data_date", ""))
        base_h2 = round(p0["revenue"] / (1 + p0["revenue_growth"]), 2)
        w.inp("rev_h2_25", lb.get("rev_comp", "2025H2 營收（2026H2 增長率基準）"), base_h2, NUM0, MU, "市場數據：" + p0["revenue_growth_base"])
        w.inp("fy26", lb.get("fy_cur", "FY2026E 營收 = 1H26 實際 + 2026H2E"), "=0", NUM0, MU, "計算：與下表 stub 營收相加；下一財年增長率以此為基準", kind="計算")
        w.inp("nwc_rate", tr("calc.b.nwc_rate", lang), resolve_nwc_rate(d), PCT, "", "分析師假設：" + d["projections"][1]["delta_nwc_rule"] + "；2026H2 = " + p0["delta_nwc_rule"] + "；" + (by.get("nwc_note") or ""))
        capex_h1 = h1.get("capex")
        if capex_h1 is not None and abs((p0["capex"] + capex_h1) - round(p0["capex"] + capex_h1)) < 1e-6:
            w.inp("capex_guide", "2026 全年資本支出指引（取中位）", round(p0["capex"] + capex_h1), NUM0, MU, "分析師假設：" + p0["reinvestment_rationale"])
            w.inp("capex_h1", "1H2026 實際資本支出", capex_h1, NUM0, MU, h1s.get("basis", "市場數據"), h1s.get("source_name", ""), h1s.get("url", ""), h1s.get("data_date", ""))
    else:
        cal = d["unit_economics_calibration"]; dr0 = p0["drivers"]
        src_ann, url_ann = "中海油 2026 中期業績公告（港交所）", cal.get("source", "")
        rs = by["revenue_source"]
        w.inp("rev_h1", "1H2026 油氣銷售收入", by["revenue"], NUM0, MU, f"市場數據：RMB{rs['value']:,.0f}m ÷ 1H 平均 USD/CNY {fx.get('usdcny_h1_avg')}", rs["source_name"], rs["url"], rs["data_date"])
        w.inp("real_ratio", "實現油價 ÷ 布蘭特", cal["realised_to_brent"], NUM4, "", "市場數據：1H26 實現油價 US$85.49 ÷ 布蘭特 US$87.6（校準）", src_ann, url_ann, "2026-06-30")
        w.inp("liq", "液體占銷量比例", cal["liquids_share"], NUM4, "", "市場數據：1H26 液體 303.6 ÷ 銷量 387.7 百萬桶油當量", src_ann, url_ann, "2026-06-30")
        w.inp("gas_mcf", "天然氣（千立方英尺／每桶油當量銷量）", cal["gas_mcf_per_boe"], NUM4, "mcf/boe", "市場數據：1H26 天然氣 492.3 bcf ÷ 銷量 387.7", src_ann, url_ann, "2026-06-30")
        w.inp("gas_px", "天然氣實現價", cal["gas_price_usd_mcf"], NUM2, "US$/mcf", "分析師假設：US$8.00/mcf（以 1H26 收入校準）", src_ann, url_ann, "2026-06-30")
        w.inp("sales_ratio", "銷量 ÷ 產量", dr0["sales_volume_mmboe"] / dr0["production_mmboe"], NUM4, "", "市場數據：1H26 銷量 387.7 ÷ 淨產量 398.7", src_ann, url_ann, "2026-06-30")
        w.inp("prod_target", "2026 全年產量目標（中位）", 790.0, NUM2, "百萬桶油當量", "市場數據：公司 2026 產量目標中位 790；2026H2 = 790 − 1H 398.7", src_ann, url_ann, "2026-06-30")
        _q9 = by.get("q3_9m_2026")
        w.inp("prod_h1", "9M2026 淨產量" if _q9 else "1H2026 淨產量", 593.7 if _q9 else 398.7, NUM2, "百萬桶油當量", "市場數據：9M26 淨產量（Q3 季報）" if _q9 else "市場數據：1H26 淨產量", src_ann, url_ann, "2026-09-30" if _q9 else "2026-06-30")
        w.inp("cash_cost", "現金成本（2026 基準）", cal["cash_cost_usd_boe"], NUM2, "US$/boe", "市場數據：1H26 作業費＋勘探＋銷管運＋其他 ÷ 1H 平均匯率 ÷ 銷量；自 2027 起每年按通脹指數上調", src_ann, url_ann, "2026-06-30")
        w.inp("oth_tax", "其他稅項占營收", cal["other_tax_pct_rev"], PCT, "", "市場數據：1H26 其他稅項 RMB12,680m ÷ 油氣銷售 RMB206,086m", src_ann, url_ann, "2026-06-30")
        w.inp("dom", "國內原油占比", cal["domestic_share"], NUM4, "", "市場數據：國內產量 275.2 ÷ 總產量 398.7（代替國內銷量拆分）", src_ann, url_ann, "2026-06-30")
        w.inp("levy_k", "特別收益金係數 k", cal["levy_k"], NUM4, "", "市場數據校準：1H26 特別收益金 RMB9,734m ÷（國內液體量 ×（實現價 − 65））；特別收益金 = 銷量 × 液體比例 × 國內比例 × k × MAX(實現價 − 65, 0)", src_ann, url_ann, "2026-06-30")
        w.inp("levy_th", "特別收益金起徵油價", 65.0, NUM2, "US$/bbl", "市場數據：門檻 US$65", src_ann, url_ann, "2026-06-30")
        w.inp("dda", "DD&A（2026 基準）", cal["dda_usd_boe"], NUM2, "US$/boe", "市場數據：1H26 DD&A RMB42,385m ÷ 1H 平均匯率 ÷ 銷量；自 2027 起按通脹指數上調", src_ann, url_ann, "2026-06-30")
        w.inp("infl", "成本年通脹", 0.02, PCT, "", "分析師假設：" + p0["margin_rationale"])
        w.inp("capex_budget", "2026 資本支出預算（中位）", 117000.0, NUM0, "RMB m", "分析師假設：" + d["projections"][1]["reinvestment_rationale"], src_ann, url_ann, "2026-06-30")
        w.inp("capex_h1", "9M2026 已支出資本支出" if _q9 else "1H2026 已支出資本支出", 89899.0 if _q9 else 61993.0, NUM0, "RMB m", by["capex_source"]["basis"], by["capex_source"]["source_name"], by["capex_source"]["url"], "2026-06-30")
        w.inp("capex_g", "資本支出年增長", 0.02, PCT, "", "分析師假設：RMB117bn × 1.02^n（維持產量及補充儲量）")
        w.inp("usdcny", "USD/CNY（資本支出及橋接換算）", fx["usdcny"], NUM4, "", "市場數據：9/30 收市", fx.get("source_name", ""), fx.get("url", ""), fx.get("data_date", ""))
        w.inp("fy26", "FY2026E 營收 = 1H26 實際 + 2026H2E", "=0", NUM0, MU, "計算：FY2027 增長率以此為基準", kind="計算")
    R = w.ref

    # ---------------------------------------------------------------- C. projections
    w.section(tr("calc.sec.c", lang), 29)
    projs = d["projections"]
    if memory:
        hdr = [tr("calc.c.year", lang), tr("calc.c.len", lang), "DRAM 位元增長", "DRAM 售價變動", "DRAM 營收", "NAND 位元增長", "NAND 售價變動", "NAND＋其他營收",
               tr("calc.c.rev", lang), tr("calc.c.rev_g", lang), tr("calc.c.ebit_m", lang), "EBIT", tr("calc.c.tax", lang), "NOPAT", "D&A", "Capex", "ΔNWC", "FCFF",
               tr("calc.c.t", lang), tr("calc.c.df", lang), "PV(FCFF)", tr("calc.c.r1", lang), tr("calc.c.r2", lang), tr("calc.c.r3", lang), tr("calc.c.src", lang)]
        col = {k: i + 1 for i, k in enumerate(["yr", "len", "db", "dasp", "drev", "nb", "nasp", "nrev", "rev", "g", "m", "ebit", "tax", "nopat", "da", "cx", "nwc",
                                                "fcff", "t", "df", "pv", "r1", "r2", "r3", "src"])}
    elif not cnooc:
        hdr = [tr("calc.c.year", lang), tr("calc.c.len", lang), tr("calc.c.rev_g", lang), tr("calc.c.rev", lang),
               tr("calc.c.ebit_m", lang), "EBIT", tr("calc.c.tax", lang), "NOPAT", tr("calc.c.da_rev", lang), "D&A",
               tr("calc.c.capex_rev", lang), "Capex", "ΔNWC", "FCFF", tr("calc.c.t", lang), tr("calc.c.df", lang),
               "PV(FCFF)", tr("calc.c.r1", lang), tr("calc.c.r2", lang), tr("calc.c.r3", lang), tr("calc.c.src", lang)]
        col = {k: i + 1 for i, k in enumerate(["yr", "len", "g", "rev", "m", "ebit", "tax", "nopat", "dap", "da", "cxp", "cx", "nwc", "fcff", "t", "df", "pv", "r1", "r2", "r3", "src"])}
    else:
        hdr = [tr("calc.c.year", lang), tr("calc.c.len", lang), "布蘭特 US$", "實現油價", "產量增長", "產量（百萬桶）", "銷量", "成本指數",
               tr("calc.c.rev", lang), tr("calc.c.rev_g", lang), "現金成本", "其他稅項", "特別收益金", "DD&A", "EBIT", tr("calc.c.ebit_m", lang),
               tr("calc.c.tax", lang), "NOPAT", "Capex（RMB m）", "Capex", "ΔNWC", "FCFF", tr("calc.c.t", lang), tr("calc.c.df", lang),
               "PV(FCFF)", tr("calc.c.r1", lang), tr("calc.c.r2", lang), tr("calc.c.r3", lang), tr("calc.c.src", lang)]
        col = {k: i + 1 for i, k in enumerate(["yr", "len", "brent", "real", "pg", "prod", "vol", "idx", "rev", "g", "cash", "otax", "levy", "da",
                                                "ebit", "m", "tax", "nopat", "cxr", "cx", "nwc", "fcff", "t", "df", "pv", "r1", "r2", "r3", "src"])}
    w.header(hdr)
    L = {k: CL(v) for k, v in col.items()}
    first = w.r
    for i, p in enumerate(projs):
        r = w.r; pr = r - 1
        c = lambda k, v, fmt=None, inp=False, wrap=False: w.cell(r, col[k], v, w.in_f if inp else w.calc_f, fmt, IN_FILL if inp else None, wrap)
        c("yr", p["year"]); ws.cell(r, 1).font = w.bold_f
        c("len", p["period_length_years"], "0.0", True)
        if memory:
            dr = p["drivers"]
            c("db", dr["dram_bit_growth"], "0.0%", True); c("dasp", dr["dram_asp_change"], "0.0%", True)
            c("nb", dr["nand_bit_growth"], "0.0%", True); c("nasp", dr["nand_asp_change"], "0.0%", True)
            pd_ = R["dram26"] if i == 0 else f"{L['drev']}{pr}"; pn_ = R["nand26"] if i == 0 else f"{L['nrev']}{pr}"
            c("drev", f"={pd_}*(1+{L['db']}{r})*(1+{L['dasp']}{r})", NUM0)
            c("nrev", f"={pn_}*(1+{L['nb']}{r})*(1+{L['nasp']}{r})", NUM0)
            c("rev", f"={L['drev']}{r}+{L['nrev']}{r}", NUM0)
            prev_rev = R["rev_fy26"] if i == 0 else f"{L['rev']}{pr}"
            c("g", f"={L['rev']}{r}/{prev_rev}-1", "0.00%")
            c("m", p["ebit_margin"], "0.00%", True)
            c("ebit", f"={L['rev']}{r}*{L['m']}{r}", NUM0)
            c("tax", f"={R['tax_rate']}", "0.00%") if abs(p["tax_rate"] - disc["tax_rate"]["value"]) < 1e-12 else c("tax", p["tax_rate"], "0.00%", True)
            c("nopat", f"={L['ebit']}{r}*(1-{L['tax']}{r})", NUM0)
            c("da", round(p["da"], 6), NUM0, True); c("cx", round(p["capex"], 6), NUM0, True)
            c("nwc", f"={R['nwc_rate']}*({L['rev']}{r}-{prev_rev})", NUM0)
        elif not cnooc:
            c("g", p["revenue_growth"], "0.00%", True)
            if i == 0:
                c("rev", f"={R['rev_h2_25']}*(1+{L['g']}{r})", NUM0)
                ws[R["fy26"].replace("$", "")] = f"={R['rev_h1']}+{L['rev']}{r}"
            elif i == 1:
                c("rev", f"={R['fy26']}*(1+{L['g']}{r})", NUM0)
            else:
                c("rev", f"={L['rev']}{pr}*(1+{L['g']}{r})", NUM0)
            c("m", p["ebit_margin"], "0.00%", True)
            c("ebit", f"={L['rev']}{r}*{L['m']}{r}", NUM0)
            c("tax", f"={R['tax_rate']}", "0.00%") if abs(p["tax_rate"] - disc["tax_rate"]["value"]) < 1e-12 else c("tax", p["tax_rate"], "0.00%", True)
            c("nopat", f"={L['ebit']}{r}*(1-{L['tax']}{r})", NUM0)
            c("dap", p["da_pct_revenue"], "0.00%", True)
            c("da", f"={L['rev']}{r}*{L['dap']}{r}", NUM0)
            cp = p["capex_pct_revenue"]
            if abs(cp * 1e4 - round(cp * 1e4)) < 1e-6:
                c("cxp", round(cp, 6), "0.00%", True); c("cx", f"={L['rev']}{r}*{L['cxp']}{r}", NUM0)
            else:
                if i == 0 and "capex_guide" in R:
                    c("cx", f"={R['capex_guide']}-{R['capex_h1']}", NUM0)
                else:
                    c("cx", p["capex"], NUM0, True)
                c("cxp", f"={L['cx']}{r}/{L['rev']}{r}", "0.00%")
            if i == 0:
                c("nwc", f"={R['nwc_rate']}*({R['fy26']}-{R['rev_fy25']})*{L['len']}{r}", NUM0)
            elif i == 1:
                c("nwc", f"={R['nwc_rate']}*({L['rev']}{r}-{R['fy26']})", NUM0)
            else:
                c("nwc", f"={R['nwc_rate']}*({L['rev']}{r}-{L['rev']}{pr})", NUM0)
        else:
            dr = p["drivers"]
            c("brent", dr["brent_usd"], NUM2, True)
            c("real", f"={L['brent']}{r}*{R['real_ratio']}", NUM2)
            if i == 0:
                c("pg", None); c("prod", f"={R['prod_target']}-{R['prod_h1']}", NUM2); c("idx", 1, NUM4, True)
                c("cxr", f"={R['capex_budget']}-{R['capex_h1']}", NUM0)
            else:
                prev_p = projs[i - 1]["drivers"]["production_mmboe"] if i > 1 else 790.0
                c("pg", round(dr["production_mmboe"] / prev_p - 1, 6), "0.0%", True)
                c("prod", f"={R['prod_target']}*(1+{L['pg']}{r})" if i == 1 else f"={L['prod']}{pr}*(1+{L['pg']}{r})", NUM2)
                c("idx", f"={L['idx']}{pr}*(1+{R['infl']})", NUM4)
                c("cxr", f"={R['capex_budget']}*(1+{R['capex_g']})" if i == 1 else f"={L['cxr']}{pr}*(1+{R['capex_g']})", NUM0)
            c("vol", f"={L['prod']}{r}*{R['sales_ratio']}", NUM2)
            c("rev", f"={L['vol']}{r}*({R['liq']}*{L['real']}{r}+{R['gas_mcf']}*{R['gas_px']})", NUM0)
            if i == 0:
                c("g", f"={L['rev']}{r}/{R['rev_h1']}-1", "0.00%")
                ws[R["fy26"].replace("$", "")] = f"={R['rev_h1']}+{L['rev']}{r}"
            elif i == 1:
                c("g", f"={L['rev']}{r}/{R['fy26']}-1", "0.00%")
            else:
                c("g", f"={L['rev']}{r}/{L['rev']}{pr}-1", "0.00%")
            c("cash", f"={L['vol']}{r}*{R['cash_cost']}*{L['idx']}{r}", NUM0)
            c("otax", f"={L['rev']}{r}*{R['oth_tax']}", NUM0)
            c("levy", f"={L['vol']}{r}*{R['liq']}*{R['dom']}*{R['levy_k']}*MAX({L['real']}{r}-{R['levy_th']},0)", NUM0)
            c("da", f"={L['vol']}{r}*{R['dda']}*{L['idx']}{r}", NUM0)
            c("ebit", f"={L['rev']}{r}-{L['cash']}{r}-{L['otax']}{r}-{L['levy']}{r}-{L['da']}{r}", NUM0)
            c("m", f"={L['ebit']}{r}/{L['rev']}{r}", "0.00%")
            c("tax", f"={R['tax_rate']}", "0.00%") if abs(p["tax_rate"] - disc["tax_rate"]["value"]) < 1e-12 else c("tax", p["tax_rate"], "0.00%", True)
            c("nopat", f"={L['ebit']}{r}*(1-{L['tax']}{r})", NUM0)
            c("cx", f"={L['cxr']}{r}/{R['usdcny']}", NUM0)
            c("nwc", 0, NUM0, True)
        c("fcff", f"={L['nopat']}{r}+{L['da']}{r}-{L['cx']}{r}-{L['nwc']}{r}", NUM0)
        if i <= 1:
            c("t", p["discount_period"], "0.00", True)
        else:
            c("t", f"={L['t']}{pr}+{L['len']}{r}", "0.00")
        c("df", f"=1/(1+{R['wacc']})^{L['t']}{r}", NUM4)
        c("pv", f"={L['fcff']}{r}*{L['df']}{r}", NUM0)
        r1 = (p.get("revenue_growth_base") or "") + ("；" if p.get("revenue_growth_base") else "") + (p.get("growth_rationale") or "")
        c("r1", r1, wrap=True); c("r2", p.get("margin_rationale"), wrap=True)
        c("r3", (p.get("reinvestment_rationale") or "") + "；ΔNWC：" + (p.get("delta_nwc_rule") or ""), wrap=True)
        c("src", "\n".join(p.get("sources") or []), wrap=True)
        for k in ("r1", "r2", "r3", "src"):
            ws.cell(r, col[k]).font = w.txt_f
        w.height(r, [(r1, 48), (p.get("margin_rationale"), 38), (p.get("reinvestment_rationale"), 38)])
        w.r += 1
    last = w.r - 1
    for k, wd in (("r1", 50), ("r2", 40), ("r3", 40), ("src", 46)):
        ws.column_dimensions[L[k]].width = wd
    rng = lambda k: f"${L[k]}${first}:${L[k]}${last}"
    w.ref.update(fcff_rng=rng("fcff"), t_rng=rng("t"), pv_rng=rng("pv"), nopat_last=f"${L['nopat']}${last}",
                 fcff_last=f"${L['fcff']}${last}", t_last=f"${L['t']}${last}")
    R = w.ref

    # ---------------------------------------------------------------- D. terminal & EV
    w.section(tr("calc.sec.d", lang))
    w.header(std_hdr, spans=std_spans)
    if term["method"] == "value_driver":
        w.inp("fcff_next", tr("calc.d.fcff_next_vd", lang), f"={R['nopat_last']}*(1+{R['g']})*(1-{R['g']}/{R['ronic']})", NUM0, MU, term.get("method_rationale", ""), kind="計算")
    else:
        w.inp("fcff_next", tr("calc.d.fcff_next_gordon", lang), f"={R['fcff_last']}*(1+{R['g']})", NUM0, MU, term.get("method_rationale", ""), kind="計算")
    w.inp("tv", tr("calc.d.tv", lang), f"={R['fcff_next']}/({R['wacc']}-{R['g']})", NUM0, MU, "計算：" + term.get("tv_timing_note", ""), kind="計算")
    w.inp("t_tv", tr("calc.d.t_tv", lang), f"={R['t_last']}", "0.00", tr("calc.unit.years", lang), "計算：" + term.get("tv_timing_note", ""), kind="計算")
    w.inp("pv_tv", tr("calc.d.pv_tv", lang), f"={R['tv']}/(1+{R['wacc']})^{R['t_tv']}", NUM0, MU, kind="計算")
    w.inp("sum_pv", tr("calc.d.sum_pv", lang), f"=SUM({R['pv_rng']})", NUM0, MU, kind="計算")
    w.inp("ev", tr("calc.d.ev", lang), f"={R['sum_pv']}+{R['pv_tv']}", NUM0, MU, key_row=True, kind="計算")
    w.inp("tv_share", tr("calc.d.tv_share", lang), f"={R['pv_tv']}/{R['ev']}", PCT, "", kind="計算")
    R = w.ref

    # ---------------------------------------------------------------- E. bridge
    w.section(tr("calc.sec.e", lang))
    e_hdr = [
        tr("calc.hdr.item", lang), "±", tr("calc.e.orig_amt", lang), tr("calc.e.fx", lang), MU,
        tr("calc.e.signed", lang), tr("calc.e.basis", lang), tr("calc.hdr.source", lang),
        tr("calc.hdr.url", lang), tr("calc.hdr.date", lang),
    ]
    e_spans = {tr("calc.e.basis", lang): 12, tr("calc.hdr.source", lang): 15, tr("calc.hdr.url", lang): 17}
    w.header(e_hdr, spans=e_spans)
    _fx_rate = fx.get("bridge_fx") if fx.get("bridge_fx") is not None else fx.get("rate")
    _same_ccy = (ccy == "USD" or d.get("model_ccy") == ccy or (_fx_rate in (None, 1, 1.0) and not fx.get("pair") and not fx.get("bridge_pair")))
    if _same_ccy and not is_adr:
        b_fx, b_note = None, SYM.get(ccy, ccy)
    elif cnooc:
        b_fx, b_note = R["usdcny"], "RMB"
    else:
        w.inp("b_fx", (fx.get("bridge_pair") or "USD/TWD") + "（橋接換算）", (fx.get("bridge_fx") if fx.get("bridge_fx") is not None else fx.get("rate", fx.get("per_share_fx", 1.0))), NUM4, "", f"市場數據：{d['price_date']} 收市", fx.get("source_name", ""), fx.get("url", ""), fx.get("data_date", ""))
        R = w.ref; b_fx, b_note = R["b_fx"], ({"CNY": "RMB"}.get(d.get("reporting_ccy"), "NT$") if d.get("reporting_ccy") else "NT$")
        w.header(e_hdr, spans=e_spans)
    b_first = w.r; debt_cells = []
    debt_items = _pick_debt(d)
    for b in d["bridge"]:
        r = w.r
        w.cell(r, 1, b["item"], w.bold_f, wrap=True)
        w.cell(r, 2, b["sign"], w.in_f, fill=IN_FILL, align="center")
        if b_fx:
            fxv = d["fx_if_any"]["usdcny"] if cnooc else ((fx.get("bridge_fx") if fx.get("bridge_fx") is not None else fx.get("rate", fx.get("per_share_fx", 1.0))))
            w.cell(r, 3, round(b["value"] * fxv, 4), w.in_f, NUM2, IN_FILL)
            w.cell(r, 4, f"={b_fx}", w.calc_f, NUM4)
            w.cell(r, 5, f"=C{r}/D{r}", w.calc_f, NUM2)
        else:
            w.cell(r, 3, b["value"], w.in_f, NUM2, IN_FILL); w.cell(r, 4, 1, w.calc_f, NUM4)
            w.cell(r, 5, f"=C{r}/D{r}", w.calc_f, NUM2)
        w.cell(r, 6, f'=IF(B{r}="+",E{r},-E{r})', w.calc_f, NUM2)
        w.text_span(r, 7, 12, b.get("basis", "")); w.text_span(r, 13, 15, b.get("source_name", ""))
        w.text_span(r, 16, 17, b.get("url", ""), link=True); w.cell(r, 18, b.get("data_date") or None, w.txt_f, align="center")
        w.height(r, [(b.get("basis", ""), 70), (b.get("source_name", ""), 34), (b["item"], 32)])
        if debt_items and b in debt_items:
            debt_cells.append(f"$E${r}")
        w.r += 1
    b_last = w.r - 1
    w.inp("bridge_net", tr("calc.e.net", lang, ccy=b_note), f"=SUM(F{b_first}:F{b_last})", NUM0, MU, kind="計算")
    R = w.ref
    # D for WACC (row reserved in block A)
    rD = w.r_debt; save_r = w.r; w.r = rD
    if debt_cells:
        w.inp("D", tr("calc.e.debt_bridge", lang), "=" + "+".join(debt_cells), NUM0, MU, disc["weight_d"]["basis"], kind="計算")
    else:
        w.inp("D", tr("calc.e.debt", lang), cs["D_model_ccy_m"], NUM0, MU, disc["weight_d"]["basis"])
    w.r = save_r; R = w.ref

    # ---------------------------------------------------------------- F. per share
    w.section(tr("calc.sec.f", lang))
    w.header(std_hdr, spans=std_spans)
    w.inp("equity", tr("calc.f.equity", lang), f"={R['ev']}+{R['bridge_net']}", NUM0, MU, kind="計算")
    if is_adr:
        w.inp("sh_common", tr("calc.f.sh_common", lang), d["shares"] * d["adr_ratio"], NUM2, u_msh, "市場數據：" + as_source_text(d["shares_source"]))
        w.inp("adr", tr("calc.f.adr", lang), d["adr_ratio"], "0", tr("calc.unit.shares", lang), "市場數據：1 ADR = 5 普通股（TSMC 股利公告）")
        w.inp("shares", tr("calc.f.adr_units", lang), f"={R['sh_common']}/{R['adr']}", NUM2, tr("calc.unit.millions", lang), kind="計算")
    else:
        w.inp("shares", tr("calc.f.shares", lang), d["shares"], NUM2, u_msh, "市場數據：" + as_source_text(d["shares_source"]))
    pfx = fx.get("per_share_fx", 1.0)
    w.inp("ps_fx", tr("calc.f.ps_fx", lang, ccy=ccy, tc=trade_currency(d)), pfx, NUM4, "", ("市場數據：" + str(fx.get("pair")) + f" {d['price_date']} 收市；" if pfx != 1 else "") + fx.get("per_share_fx_note", ""),
          fx.get("source_name", "") if pfx != 1 else "", fx.get("url", "") if pfx != 1 else "", fx.get("data_date", "") if pfx != 1 else "")
    R = w.ref
    w.inp("fair", tr("calc.f.fair", lang, sym=sym), f"={R['equity']}/{R['shares']}*{R['ps_fx']}", NUM2, sym, key_row=True, kind="計算")
    R = w.ref
    w.inp("fair_json", tr("calc.f.fair_json", lang), d["fair_value_per_share"], NUM2, sym, f"calc/{t}_latest.json → fair_value_per_share", kind="輸入")
    R = w.ref
    w.inp("diff", tr("calc.f.diff", lang), f"={R['fair']}/{R['fair_json']}-1", "0.0000%", "", kind="計算")
    R = w.ref
    chk_pass, chk_fail = tr("calc.f.check_pass", lang), tr("calc.f.check_fail", lang)
    w.inp("check", tr("calc.f.check", lang), f'=IF(ABS({R["diff"]})<0.005,"{chk_pass}","{chk_fail}")', None, "", kind="計算")
    if is_adr and peer:
        R = w.ref
        pr_ = peer["refs"]
        w.inp("xchk", "交叉核對：2330.TW 理論價 × ADR 比率 ÷ USD/TWD", f"='{peer['name']}'!{pr_['fair']}*{R['adr']}/'{peer['name']}'!{pr_['b_fx']}", NUM2, "US$",
              "計算：" + d.get("consistency_check", ""), kind="計算")
    R = w.ref
    w.inp("price", tr("calc.f.price", lang, date=d["price_date"]), d["price"], NUM2, sym, "市場數據", "", d["price_source_url"], d["price_date"])
    R = w.ref
    w.inp("ratio", tr("calc.f.ratio", lang), f"={R['price']}/{R['fair']}", MULT, "", tr("calc.f.ratio_note", lang), kind="計算")
    R = w.ref
    w.inp("gap", tr("calc.f.gap", lang), f"={R['ratio']}-1", "+0.0%;-0.0%;0.0%", "", tr("calc.f.gap_note", lang), kind="計算")
    R = w.ref
    gcell = R["gap"].replace("$", "")
    ws.conditional_formatting.add(gcell, CellIsRule(operator="lessThan", formula=["0"], fill=GREEN[0], font=w.green_f))
    ws.conditional_formatting.add(gcell, CellIsRule(operator="greaterThan", formula=["0"], fill=RED[0], font=w.red_f))
    ccell = R["check"].replace("$", "")
    ws.conditional_formatting.add(ccell, CellIsRule(operator="equal", formula=[f'"{chk_pass}"'], fill=GREEN[0], font=w.green_f))
    ws.conditional_formatting.add(ccell, CellIsRule(operator="equal", formula=[f'"{chk_fail}"'], fill=RED[0], font=w.red_f))

    # ---------------------------------------------------------------- G. sensitivity
    sens = d["sensitivity"]
    w.section(tr("calc.sec.g", lang, sym=sym))
    tvf = (lambda wc, gc: f"{R['nopat_last']}*(1+{gc})*(1-{gc}/{R['ronic']})") if term["method"] == "value_driver" \
        else (lambda wc, gc: f"{R['fcff_last']}*(1+{gc})")
    hr = w.r
    w.cell(hr, 1, "WACC ＼ g", w.hdr_f, fill=HDR_FILL, align="center")
    for j, g in enumerate(sens["g_list"]):
        w.cell(hr, 2 + j, g, w.in_f, "0.0%", IN_FILL, align="center")
    w.r += 1
    base_w = disc["wacc"]["value"]
    live_first = w.r
    for i, wv in enumerate(sens["wacc_list"]):
        r = w.r; off = round(wv - base_w, 6)
        w.cell(r, 1, f"={R['wacc']}{'+' if off >= 0 else '-'}{abs(off)}", w.calc_f, "0.00%", align="center")
        for j in range(len(sens["g_list"])):
            wc, gc = f"$A{r}", f"{CL(2 + j)}${hr}"
            f = (f"=(SUMPRODUCT({R['fcff_rng']}/(1+{wc})^{R['t_rng']})+{tvf(wc, gc)}/({wc}-{gc})/(1+{wc})^{R['t_last']}"
                 f"+{R['bridge_net']})/{R['shares']}*{R['ps_fx']}")
            x = w.cell(r, 2 + j, f, w.calc_f, NUM2)
            if abs(off) < 1e-9 and abs(sens["g_list"][j] - term["g"]) < 1e-12:
                x.fill = KEY_FILL; x.font = w.key_f
        w.r += 1
    live_last = w.r - 1
    w.r += 1
    x = ws.cell(w.r, 1, tr("calc.g.json_matrix", lang)); x.font = w.hdr_f; w.r += 1
    j_first = w.r
    for i, wv in enumerate(sens["wacc_list"]):
        w.cell(w.r, 1, wv, w.in_f, "0.00%", IN_FILL, align="center")
        for j, v in enumerate(sens["matrix"][i]):
            w.cell(w.r, 2 + j, v, w.in_f, NUM2, IN_FILL)
        w.r += 1
    j_last = w.r - 1
    w.inp("sens_diff", tr("calc.g.sens_diff", lang), f"=SUMPRODUCT(ABS(B{live_first}:F{live_last}-B{j_first}:F{j_last}))/25", NUM4, sym, kind="計算")
    R = w.ref

    # ---------------------------------------------------------------- H. scenarios / revisions / deviations
    w.section(tr("calc.sec.h", lang))
    h_changes = tr("calc.h.changes", lang)
    w.header([tr("wb.summary.hdr.scenario", lang), tr("wb.scenarios.hdr.fv", lang),
              tr("calc.h.rel_base", lang), h_changes], spans={h_changes: 14})
    for s in d["scenarios"]:
        r = w.r
        disp_s = scenario_name(s["name"], lang)
        w.cell(r, 1, disp_s, w.bold_f, wrap=True)
        w.cell(r, 2, s["fair_value"], w.in_f, NUM2, IN_FILL)
        w.cell(r, 3, f"=B{r}/{R['fair_json']}-1", w.calc_f, "+0.0%;-0.0%;0.0%")
        w.text_span(r, 4, 14, s.get("changes", ""))
        w.height(r, [(s.get("changes", ""), 120), (disp_s, 32)])
        w.r += 1
    w.section(tr("calc.sec.revisions", lang))
    rev_old, rev_new, rev_reason = tr("calc.rev.old", lang), tr("calc.rev.new", lang), tr("calc.rev.reason", lang)
    w.header([tr("calc.rev.date_item", lang), rev_old, rev_new, rev_reason],
             spans={rev_old: 5, rev_new: 9, rev_reason: 18})
    for rv in d["revisions"]:
        r = w.r
        w.cell(r, 1, f"{rv['date']}｜{rv['item']}", w.bold_f, wrap=True)
        fmtv = lambda v: (f"{v:.4%}" if isinstance(v, float) and v < 1 else (f"{v:,.2f}" if isinstance(v, (int, float)) else str(v)))
        w.text_span(r, 2, 5, fmtv(rv["old"])); w.text_span(r, 6, 9, fmtv(rv["new"])); w.text_span(r, 10, 18, rv.get("reason", ""))
        w.height(r, [(fmtv(rv["old"]), 46), (fmtv(rv["new"]), 46), (rv.get("reason", ""), 100), (rv["item"], 30)])
        w.r += 1
    w.section(tr("calc.sec.deviations", lang))
    dev_req, dev_adp, dev_rsn = tr("calc.dev.requested", lang), tr("calc.dev.adopted", lang), tr("calc.dev.reason", lang)
    w.header([tr("calc.hdr.item", lang), dev_req, dev_adp, dev_rsn],
             spans={dev_req: 5, dev_adp: 10, dev_rsn: 18})
    for sd in d.get("spec_deviations", []):
        r = w.r
        w.cell(r, 1, sd["item"], w.bold_f, wrap=True)
        w.text_span(r, 2, 5, sd["requested"]); w.text_span(r, 6, 10, sd["adopted"]); w.text_span(r, 11, 18, sd["reason"])
        w.height(r, [(sd["requested"], 46), (sd["adopted"], 58), (sd["reason"], 90), (sd["item"], 30)])
        w.r += 1
    ws.freeze_panes = "B5"
    ws.sheet_view.zoomScale = 90
    ws.page_setup.orientation = "landscape"; ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.sheet_properties.tabColor = "1F3A5F"
    return {"name": name, "refs": dict(w.ref), "json": d}
