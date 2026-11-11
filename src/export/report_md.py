"""Traditional Chinese markdown valuation report."""
from __future__ import annotations


def write_report(d: dict, path: str) -> str:
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
    a(f"# {d['asset']}（{d['ticker']}）FCFF 估值")
    a("")
    a(f"- 估值日：{d['valuation_date']}（亞洲／澳門時間，UTC+8）")
    a(f"- 市價：{tc} {px}（{d['price_date']}，來源：{d.get('price_source_url')}）")
    a(f"- 主理論價（基準）：{tc} {fv:.4f}")
    a(f"- 模型貨幣：{cc}" + (f"（財報以 {cc} 列帳，每股數值按匯率換算為 {tc}）" if cc != tc else ""))
    a(f"- 市價／基準理論價：{ratio:.3f}×" if ratio else "- 市價／基準理論價：不適用")
    a(f"- WACC（方法 A）：{wacc:.2%}")
    a(f"- 終值 g：{g:.2%}")
    a(f"- 假設來源：{d.get('assumption_source')}")
    a("")
    a("**本文件不構成投資建議。** 理論價由本系統 Python 引擎按已列來源與假設計算，不是語言模型的記憶或心算。投行目標價只作交叉核對，不用來反向修正本框架的 WACC、增長或利潤率。本次為臨時估值，**沒有**寫入關注名單或交易訊號。")
    a("")
    a("## 五情境每股理論價")
    a("")
    a("| 情境 | 理論價 | 市價／該情境 | 說明 |")
    a("| --- | ---: | ---: | --- |")
    for name in ["悲觀", "基準", "樂觀", "靜態", "9%靜態"]:
        s = scen.get(name) or {}
        v = s.get("fair_value")
        r = f"{px/v:.3f}×" if v else ""
        a(f"| {name} | {cc} {v:.4f} | {r} | {s.get('changes','')} |" if v else f"| {name} | — |  |  |")
    a("")
    a("## 折現率（方法 A）")
    a("")
    disc = d["discount"]
    def v(k):
        x = disc[k]
        return f"{x['value']:.4%}；{x.get('basis','')}（{x.get('source_name','')}，{x.get('data_date','')}）"
    a(f"- Rf：{v('rf')}")
    a(f"- ERP：{v('erp')}")
    a(f"- CRP：{v('crp')}")
    a(f"- 採用 β：{v('beta_adopted')}")
    a(f"- 參考迴歸 β：{v('beta_ref')}")
    a(f"- Ke：{v('ke')}")
    a(f"- Kd（稅前）：{v('kd_pre_tax')}")
    a(f"- 稅率：{v('tax_rate')}")
    a(f"- WACC：{v('wacc')}")
    a("")
    a("## 基準增長與利潤率")
    a("")
    a("| 財年 | 營收增長 | 營收 | EBIT 利潤率 | Capex/營收 | 折現期 t |")
    a("| --- | ---: | ---: | ---: | ---: | ---: |")
    for p in d.get("projections") or []:
        a(f"| {p['year']} | {p['revenue_growth']:.2%} | {p['revenue']:,.1f} | {p['ebit_margin']:.2%} | {p['capex_pct_revenue']:.2%} | {p['discount_period']:.3f} |")
    a("")
    a("## 增長假設對照（摘要）")
    a("")
    gt = d.get("growth_table") or {}
    a("完整逐年對照見 Excel 工作表「增長假設對照」。一致預期或投行欄若無來源數字則留空。")
    for n in gt.get("notes") or []:
        a(f"- {n}")
    a("")
    a("## 投行估值比較")
    a("")
    ib = d.get("ib_compare") or {}
    cons = ib.get("consensus") or {}
    if cons.get("mean") is not None:
        a(f"- 一致預期目標價均值 {cons.get('currency') or cc} {cons['mean']}（區間 {cons.get('low')}–{cons.get('high')}，n={cons.get('n_analysts')}，{cons.get('source')}，{cons.get('data_date')}）。均值 ÷ 本框架基準 = {cons['mean']/fv:.3f}×。")
    else:
        a("- 未取得可用的一致預期目標價，或覆蓋為零；不填假數。")
    brokers = ib.get("named_brokers") or []
    if not brokers:
        a("- 無具名投行目標價（未取得報告原文或可核對轉述）。")
    else:
        for b in brokers:
            a(f"- {b.get('broker')}：目標價 {b.get('target')}（{b.get('report_date')}，{b.get('rating')}）。方法：{b.get('method') or '未披露'}。")
    if len(brokers) < 5:
        a("- 具名機構少於五家，樣本不足。")
    a("")
    a("## 資料缺口")
    a("")
    for g in d.get("data_gaps") or []:
        a(f"- {g}")
    a("")
    a("## CFO 與 NOPAT 交叉（若有）")
    a("")
    x = d.get("cfo_fcff_crosscheck")
    if x:
        a(f"- 年度：{x.get('year')}；CFO 公式 FCFF≈{x.get('fcff_cfo_formula')}；NOPAT={x.get('nopat')}；說明：{x.get('note')}")
    else:
        a("- 本次無完整交叉，或已在缺口說明。")
    a("")
    a("## 驗證")
    a("")
    a(f"- 獨立重算：見同目錄 verify 結果。算術通過只代表內部一致，不代表假設在估值日仍然完整。")
    a("")
    return path if _dump(path, "\n".join(lines) + "\n") else path


def _dump(path, text):
    open(path, "w", encoding="utf-8").write(text)
    return True
