"""Multi-language markdown valuation report (zh-Hant, zh-Hans, en)."""
from __future__ import annotations

from .i18n import DEFAULT, normalize_lang, render_gaps, scenario_name, t


def write_report(d: dict, path: str, lang: str = DEFAULT) -> str:
    lang = normalize_lang(lang)
    cc = d["currency"]
    from .xlsx_calc import trade_currency

    tc = trade_currency(d)
    fv = d["fair_value_per_share"]
    px = d["price"]
    ratio = px / fv if fv else None
    wacc = d["discount"]["wacc"]["value"]
    g = d["terminal"]["g"]
    scen = {s["name"]: s for s in d.get("scenarios") or []}
    lines = []
    a = lines.append
    a(t("report.title", lang, asset=d["asset"], ticker=d["ticker"]))
    a("")
    a(t("report.valuation_date", lang, date=d["valuation_date"]))
    a(t("report.price", lang, tc=tc, px=px, date=d["price_date"], url=d.get("price_source_url")))
    a(t("report.fair_value", lang, tc=tc, fv=fv))
    fx_note = t("report.model_ccy_fx_note", lang, cc=cc, tc=tc) if cc != tc else ""
    a(t("report.model_ccy", lang, cc=cc) + fx_note)
    a(t("report.price_over_fv", lang, ratio=ratio) if ratio else t("report.price_over_fv_na", lang))
    a(t("report.wacc", lang, wacc=wacc))
    a(t("report.terminal_g", lang, g=g))
    a(t("report.assumption_source", lang, source=d.get("assumption_source")))
    a("")
    a(t("report.disclaimer", lang))
    a("")
    a(t("report.sec.scenarios", lang))
    a("")
    a(t("report.tbl.scenarios", lang))
    a("| --- | ---: | ---: | --- |")
    mult_suffix = "x" if lang == "en" else "×"
    for name in ["悲觀", "基準", "樂觀", "靜態", "9%靜態"]:
        s = scen.get(name) or {}
        v = s.get("fair_value")
        r = f"{px/v:.3f}{mult_suffix}" if v else ""
        disp_name = scenario_name(name, lang)
        a(f"| {disp_name} | {cc} {v:.4f} | {r} | {s.get('changes','')} |" if v else f"| {disp_name} | — |  |  |")
    a("")
    a(t("report.sec.discount", lang))
    a("")
    disc = d["discount"]

    def v(k):
        x = disc[k]
        return t("report.disc.fmt", lang, value=x["value"], basis=x.get("basis", ""),
                 source=x.get("source_name", ""), date=x.get("data_date", ""))

    for key in ("rf", "erp", "crp", "beta_adopted", "beta_ref", "ke", "kd_pre_tax", "tax_rate", "wacc"):
        a(t(f"report.disc.{key}", lang, val=v(key)))
    a("")
    a(t("report.sec.projections", lang))
    a("")
    a(t("report.tbl.projections", lang))
    a("| --- | ---: | ---: | ---: | ---: | ---: |")
    for p in d.get("projections") or []:
        a(f"| {p['year']} | {p['revenue_growth']:.2%} | {p['revenue']:,.1f} | {p['ebit_margin']:.2%} | {p['capex_pct_revenue']:.2%} | {p['discount_period']:.3f} |")
    a("")
    a(t("report.sec.growth", lang))
    a("")
    gt = d.get("growth_table") or {}
    a(t("report.growth.hint", lang))
    for n in gt.get("notes") or []:
        a(f"- {n}")
    a("")
    a(t("report.sec.ib", lang))
    a("")
    ib = d.get("ib_compare") or {}
    cons = ib.get("consensus") or {}
    if cons.get("mean") is not None:
        a(t("report.ib.consensus", lang, ccy=cons.get("currency") or cc, mean=cons["mean"],
            low=cons.get("low"), high=cons.get("high"), n=cons.get("n_analysts"),
            source=cons.get("source"), date=cons.get("data_date"), ratio=cons["mean"] / fv))
    else:
        a(t("report.ib.no_consensus", lang))
    brokers = ib.get("named_brokers") or []
    if not brokers:
        a(t("report.ib.no_brokers", lang))
    else:
        for b in brokers:
            a(t("report.ib.broker_line", lang, broker=b.get("broker"), target=b.get("target"),
                date=b.get("report_date"), rating=b.get("rating"),
                method=b.get("method") or t("common.undisclosed", lang)))
    if len(brokers) < 5:
        a(t("report.ib.small_sample", lang))
    a("")
    a(t("report.sec.gaps", lang))
    a("")
    for gap_text in render_gaps(d, lang):
        a(f"- {gap_text}")
    a("")
    a(t("report.sec.crosscheck", lang))
    a("")
    x = d.get("cfo_fcff_crosscheck")
    if x:
        a(t("report.crosscheck.line", lang, year=x.get("year"), fcff=x.get("fcff_cfo_formula"),
            nopat=x.get("nopat"), note=x.get("note")))
    else:
        a(t("report.crosscheck.none", lang))
    a("")
    a(t("report.sec.verify", lang))
    a("")
    a(t("report.verify.line", lang))
    a("")
    return path if _dump(path, "\n".join(lines) + "\n") else path


def _dump(path, text):
    open(path, "w", encoding="utf-8").write(text)
    return True
