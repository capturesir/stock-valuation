/* v1.1 research UI: valuation history chart, statement review with adjustments,
   history-vs-forecast, prediction reasons, AI usage.
   Loaded after app.js; exposes window.Research. No framework, no build step. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const fmtN = (v, d = 2) => (v == null || !Number.isFinite(Number(v)))
    ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtPct = (v, d = 1) => (v == null || !Number.isFinite(Number(v))) ? "—" : (Number(v) * 100).toFixed(d) + "%";
  const currencyOf = (c) => ({ USD: "US$", HKD: "HK$", CNY: "¥", EUR: "€", JPY: "¥", GBP: "£", TWD: "NT$" }[c] || "");

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  const state = {
    research: null,
    meta: null,
    ticker: null,
    reviewPeriod: "annual",
    reviewStatement: "is",
    reviewFilter: "",
    reviewModelOnly: false,
    chart: { selected: null, compare: [], zoom: "all", points: [] },
    forecastOpen: null,
  };

  // ---------------------------------------------------------------- chart ---
  const SVG_NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => {
    const n = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    return n;
  };

  function chartSeries(points) {
    // R20: several valuations on one day stay visible — offset them slightly and
    // label the group instead of merging (merging would drop data).
    const byDate = {};
    points.forEach((p) => { (byDate[p.date] = byDate[p.date] || []).push(p); });
    return points.map((p) => {
      const group = byDate[p.date];
      const idx = group.indexOf(p);
      return { ...p, jitter: group.length > 1 ? (idx - (group.length - 1) / 2) * 0.55 : 0, groupSize: group.length };
    });
  }

  function visiblePoints() {
    const all = chartSeries(state.chart.points || []);
    if (state.chart.zoom === "all") return all;
    const n = state.chart.zoom === "1y" ? null : 5;
    if (n) return all.slice(-n);
    const last = new Date(all[all.length - 1]?.date || Date.now());
    const cut = new Date(last.getTime() - 365 * 864e5);
    return all.filter((p) => new Date(p.date) >= cut);
  }

  function renderChart() {
    const host = $("chart-block");
    if (!host) return;
    host.innerHTML = "";
    const data = state.research?.history;
    const head = el("div", "block-head");
    head.appendChild(el("h3", null, "估值歷史：理論價 vs 當時市價"));
    const tools = el("div", "seg");
    [["all", "全部"], ["1y", "近一年"], ["last5", "近五次"]].forEach(([k, label]) => {
      const b = el("button", "seg-btn" + (state.chart.zoom === k ? " on" : ""), label);
      b.type = "button";
      b.addEventListener("click", () => { state.chart.zoom = k; renderChart(); });
      tools.appendChild(b);
    });
    head.appendChild(tools);
    host.appendChild(head);

    if (!data || !data.points || !data.points.length) {
      host.appendChild(el("p", "muted", "還沒有估值歷史。每次按「估值」都會新增一個點（理論價 + 當時市價），圖表隨時間累積。"));
      return;
    }

    const pts = visiblePoints();
    const W = 960, H = 340, M = { l: 62, r: 18, t: 18, b: 46 };
    const fairVals = pts.map((p) => p.fair_value);
    const priceVals = pts.map((p) => p.price);
    let lo = Math.min(...fairVals, ...priceVals);
    let hi = Math.max(...fairVals, ...priceVals);
    const pad = Math.max((hi - lo) * 0.12, hi * 0.02);
    lo = Math.max(0, lo - pad); hi += pad;
    const t0 = new Date(pts[0].date).getTime();
    const t1 = new Date(pts[pts.length - 1].date).getTime();
    const span = Math.max(1, t1 - t0);
    const x = (p) => pts.length === 1
      ? M.l + (W - M.l - M.r) / 2
      : M.l + ((new Date(p.date).getTime() - t0) / span) * (W - M.l - M.r) + p.jitter * 8;
    const y = (v) => M.t + (1 - (v - lo) / Math.max(1e-9, hi - lo)) * (H - M.t - M.b);

    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img",
      "aria-label": "估值歷史圖：理論價與當時市價" });

    // grid + y labels
    for (let i = 0; i <= 4; i++) {
      const v = lo + (hi - lo) * (i / 4);
      const yy = y(v);
      svg.appendChild(svgEl("line", { x1: M.l, x2: W - M.r, y1: yy, y2: yy, class: "chart-grid" }));
      const t = svgEl("text", { x: M.l - 8, y: yy + 4, class: "chart-axis", "text-anchor": "end" });
      t.textContent = fmtN(v, v > 1000 ? 0 : 2);
      svg.appendChild(t);
    }

    // R18: fill between the two lines, green where fair > price, red where below
    const region = svgEl("g", { class: "chart-region" });
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      const up = (a.fair_value + b.fair_value) / 2 >= (a.price + b.price) / 2;
      const poly = svgEl("polygon", {
        points: [[x(a), y(a.fair_value)], [x(b), y(b.fair_value)], [x(b), y(b.price)], [x(a), y(a.price)]]
          .map((q) => q.join(",")).join(" "),
        class: up ? "chart-fill up" : "chart-fill down",
      });
      region.appendChild(poly);
    }
    svg.appendChild(region);

    const line = (key, cls) => svgEl("polyline", {
      points: pts.map((p) => `${x(p)},${y(p[key])}`).join(" "), class: cls,
    });
    svg.appendChild(line("price", "chart-line price"));
    svg.appendChild(line("fair_value", "chart-line fair"));

    // x labels: first, middle, last only, so dense dates do not overlap
    const labelIdx = pts.length <= 3 ? pts.map((_, i) => i) : [0, Math.floor((pts.length - 1) / 2), pts.length - 1];
    labelIdx.forEach((i) => {
      const t = svgEl("text", { x: x(pts[i]), y: H - M.b + 20, class: "chart-axis", "text-anchor": "middle" });
      t.textContent = pts[i].date;
      svg.appendChild(t);
    });

    // points: big invisible hit area for finger/mouse (R21)
    pts.forEach((p) => {
      const g = svgEl("g", { class: "chart-point", tabindex: "0", role: "button",
        "aria-label": `${p.date} 理論價 ${fmtN(p.fair_value)} 市價 ${fmtN(p.price)}` });
      g.appendChild(svgEl("circle", { cx: x(p), cy: y(p.price), r: 14, class: "hit" }));
      g.appendChild(svgEl("circle", { cx: x(p), cy: y(p.price), r: 5, class: "dot price" }));
      g.appendChild(svgEl("circle", { cx: x(p), cy: y(p.fair_value), r: 14, class: "hit" }));
      g.appendChild(svgEl("circle", { cx: x(p), cy: y(p.fair_value), r: 5, class: "dot fair" }));
      if (p.groupSize > 1) {
        const badge = svgEl("text", { x: x(p) + 9, y: y(p.fair_value) - 10, class: "chart-badge" });
        badge.textContent = `×${p.groupSize}`;
        g.appendChild(badge);
      }
      const pick = (ev) => { ev.preventDefault(); selectPoint(p); };
      g.addEventListener("click", pick);
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") pick(ev); });
      svg.appendChild(g);
    });

    if (state.chart.selected) {
      const p = state.chart.selected;
      svg.appendChild(svgEl("line", { x1: x(p), x2: x(p), y1: M.t, y2: H - M.b, class: "chart-cursor" }));
    }

    const wrap = el("div", "chart-wrap");
    wrap.appendChild(svg);
    host.appendChild(wrap);

    const legend = el("div", "chart-legend");
    legend.appendChild(el("span", "lg fair", "理論價"));
    legend.appendChild(el("span", "lg price", "當時市價"));
    legend.appendChild(el("span", "lg up", "理論價高於股價（低估區間）"));
    legend.appendChild(el("span", "lg down", "理論價低於股價（高估區間）"));
    if (pts.some((p) => p.groupSize > 1)) legend.appendChild(el("span", "lg note", "×N = 同日多次估值"));
    host.appendChild(legend);

    // navigation + compare (R21)
    const nav = el("div", "chart-nav");
    const cur = state.chart.selected ? pts.findIndex((p) => p.date === state.chart.selected.date && p.fair_value === state.chart.selected.fair_value) : -1;
    const mk = (label, fn, disabled) => {
      const b = el("button", "btn small", label);
      b.type = "button"; b.disabled = !!disabled; b.addEventListener("click", fn);
      nav.appendChild(b);
    };
    mk("◀ 上一筆", () => selectPoint(pts[Math.max(0, (cur < 0 ? pts.length : cur) - 1)]), pts.length < 2 || cur === 0);
    mk("下一筆 ▶", () => selectPoint(pts[Math.min(pts.length - 1, cur + 1)]), pts.length < 2 || cur === pts.length - 1);
    mk(state.chart.compare.length === 1 ? "選擇第二筆比較" : "比較兩筆", () => {
      state.chart.compare = [];
      state.chart.compareMode = true;
      setChartHint("比較模式：依次點選兩個資料點，會列出兩次估值的差異。");
    });
    mk("清除選取", () => { state.chart.selected = null; state.chart.compare = []; state.chart.compareMode = false; renderChart(); }, !state.chart.selected && !state.chart.compare.length);
    host.appendChild(nav);
    host.appendChild($("chart-detail") || el("div", "chart-detail", "")).id = "chart-detail";
    host.appendChild(el("p", "hint", data.note || ""));

    const detail = $("chart-detail");
    detail.innerHTML = "";
    if (state.chart.compare.length === 2) {
      detail.appendChild(compareTable(state.chart.compare));
    } else if (state.chart.selected) {
      detail.appendChild(pointCard(state.chart.selected));
    }
    if (state.chart.hint) host.appendChild(el("p", "hint", state.chart.hint));
  }

  function setChartHint(text) {
    state.chart.hint = text;
    renderChart();
  }

  function selectPoint(p) {
    if (!p) return;
    if (state.chart.compareMode) {
      state.chart.compare = [...state.chart.compare, p].slice(-2);
      if (state.chart.compare.length === 2) state.chart.compareMode = false;
    }
    state.chart.selected = p;
    state.chart.hint = "";
    renderChart();
  }

  function pointCard(p) {
    const box = el("div", "chart-card");
    const head = el("div", "chart-card-head");
    head.appendChild(el("strong", null, `估值日 ${p.date}`));
    const cur = p.currency || state.meta?.currency || "";
    head.appendChild(el("span", "badge " + (p.gap >= 0 ? "ok" : "warn"), p.gap >= 0 ? "理論價較高" : "理論價較低"));
    box.appendChild(head);
    const facts = [
      ["理論價", `${currencyOf(cur)}${fmtN(p.fair_value, 2)}`],
      ["當時市價", `${currencyOf(cur)}${fmtN(p.price, 2)}`],
      ["差距", `${p.gap >= 0 ? "+" : ""}${fmtN(p.gap, 2)}（${fmtPct(p.gap_pct)}）`],
      ["WACC / 終值 g", `${fmtPct(p.wacc, 2)} / ${fmtPct(p.g, 2)}`],
      ["驗證", p.verify_pass === true ? "通過" : p.verify_pass === false ? "未通過" : "—"],
      ["假設來源", p.assumption_source || "—"],
      ["市價日期", p.price_date || "—"],
    ];
    const dl = el("dl", "facts compact");
    facts.forEach(([k, v]) => {
      const w = el("div");
      w.appendChild(el("dt", null, k));
      w.appendChild(el("dd", null, v));
      dl.appendChild(w);
    });
    box.appendChild(dl);
    if (p.groupSize > 1) box.appendChild(el("p", "hint", `同日共有 ${p.groupSize} 次估值，圖上左右錯開顯示，沒有合併或刪除。`));
    return box;
  }

  function compareTable([a, b]) {
    const box = el("div", "chart-card");
    box.appendChild(el("strong", null, `比較 ${a.date} 與 ${b.date}`));
    const rows = [
      ["理論價", fmtN(a.fair_value, 2), fmtN(b.fair_value, 2), fmtN(b.fair_value - a.fair_value, 2)],
      ["市價", fmtN(a.price, 2), fmtN(b.price, 2), fmtN(b.price - a.price, 2)],
      ["差距", fmtN(a.gap, 2), fmtN(b.gap, 2), fmtN(b.gap - a.gap, 2)],
      ["差距%", fmtPct(a.gap_pct), fmtPct(b.gap_pct), fmtPct((b.gap_pct || 0) - (a.gap_pct || 0))],
      ["WACC", fmtPct(a.wacc, 2), fmtPct(b.wacc, 2), fmtPct((b.wacc || 0) - (a.wacc || 0), 2)],
    ];
    const t = el("table", "mini");
    const thead = el("thead");
    const hr = el("tr");
    ["項目", a.date, b.date, "變化"].forEach((h) => hr.appendChild(el("th", null, h)));
    thead.appendChild(hr); t.appendChild(thead);
    const tb = el("tbody");
    rows.forEach((r) => {
      const tr = el("tr");
      r.forEach((c) => tr.appendChild(el("td", null, c)));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    box.appendChild(t);
    return box;
  }

  // --------------------------------------------------------------- review ---
  function renderReview() {
    const host = $("review-block");
    if (!host) return;
    const review = state.research?.review;
    host.innerHTML = "";
    const summary = el("summary", "review-summary");
    summary.appendChild(el("strong", null, "財報逐項覆核與調整"));
    const count = review && review.count ? `${review.count} 個科目` : "尚未收集財報";
    summary.appendChild(el("span", "muted", `（${count}；修改會影響下次估值並永久留痕）`));
    host.appendChild(summary);

    if (!review || !review.ok) {
      host.appendChild(el("p", "muted", review?.error || "尚未收集財報。"));
      return;
    }

    const body = el("div", "review-body");
    const bar = el("div", "review-bar");
    const stTabs = el("div", "seg");
    review.statements.forEach((s) => {
      const b = el("button", "seg-btn" + (state.reviewStatement === s.key ? " on" : ""), `${s.label}（${s.count}）`);
      b.type = "button";
      b.addEventListener("click", () => { state.reviewStatement = s.key; renderReview(); });
      stTabs.appendChild(b);
    });
    bar.appendChild(stTabs);

    const search = el("input", "input small");
    search.type = "search";
    search.placeholder = "搜尋科目（中／英）";
    search.value = state.reviewFilter;
    search.addEventListener("input", () => { state.reviewFilter = search.value; renderReview(); });
    bar.appendChild(search);

    const lbl = el("label", "check small");
    const cb = el("input");
    cb.type = "checkbox"; cb.checked = state.reviewModelOnly;
    cb.addEventListener("change", () => { state.reviewModelOnly = cb.checked; renderReview(); });
    lbl.appendChild(cb);
    lbl.appendChild(el("span", null, "只看模型輸入"));
    bar.appendChild(lbl);
    body.appendChild(bar);

    const stmt = review.statements.find((s) => s.key === state.reviewStatement) || review.statements[0];
    if (!stmt) { host.appendChild(body); return; }
    const q = state.reviewFilter.trim().toLowerCase();
    const rows = stmt.rows.filter((r) => {
      if (state.reviewModelOnly && !r.model_input) return false;
      if (!q) return true;
      return (r.label + " " + r.item).toLowerCase().includes(q);
    });

    const adjs = state.research.adjustments || [];
    const adjFor = (r, fy) => adjs.filter((a) => a.statement === stmt.key && a.item === r.item && a.fy === fy).slice(-1)[0];

    const table = el("table", "review-table");
    const thead = el("thead");
    const hr = el("tr");
    hr.appendChild(el("th", null, "科目"));
    hr.appendChild(el("th", null, "單位"));
    stmt.years.forEach((y) => hr.appendChild(el("th", "num", y)));
    hr.appendChild(el("th", null, "重要性"));
    hr.appendChild(el("th", null, "模型"));
    hr.appendChild(el("th", null, ""));
    thead.appendChild(hr);
    table.appendChild(thead);

    const tb = el("tbody");
    rows.forEach((r) => {
      const tr = el("tr");
      if (r.model_input) tr.classList.add("model-input");
      if (stmt.years.some((y) => adjFor(r, y))) tr.classList.add("adjusted");
      const name = el("td");
      name.appendChild(el("div", null, r.label));
      name.appendChild(el("div", "sub", r.item));
      tr.appendChild(name);
      tr.appendChild(el("td", null, r.unit));
      stmt.years.forEach((y) => {
        const adj = adjFor(r, y);
        const td = el("td", "num");
        td.textContent = fmtN(r.values[y], r.kind === "ratio" || r.kind === "per_share" ? 4 : 1);
        if (adj) {
          td.classList.add("has-adj");
          td.title = `已修改：${adj.original} → ${adj.adjusted}（${adj.reason}）`;
        }
        tr.appendChild(td);
      });
      tr.appendChild(el("td", null, `${r.rank}/${r.rank_of}`));
      tr.appendChild(el("td", null, r.model_input ? "是" : ""));
      const act = el("td");
      const edit = el("button", "btn tiny", "修改");
      edit.type = "button";
      edit.addEventListener("click", () => openEditor(tr, stmt, r));
      act.appendChild(edit);
      tr.appendChild(act);
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    body.appendChild(table);
    body.appendChild(el("p", "hint", review.note || ""));

    // adjustment history (R07)
    const list = el("div", "adj-list");
    list.appendChild(el("h3", null, `修改紀錄（${adjs.length}）`));
    if (!adjs.length) {
      list.appendChild(el("p", "muted", "還沒有修改。原值與修改值都會保留，沒有原因的修改不會被採用。"));
    } else {
      const t = el("table", "mini");
      const th = el("thead"), trh = el("tr");
      ["時間", "科目", "財報期", "修改前", "修改後", "原因", "對估值的影響", ""].forEach((h) => trh.appendChild(el("th", null, h)));
      th.appendChild(trh); t.appendChild(th);
      const tbody = el("tbody");
      [...adjs].reverse().forEach((a) => {
        const tr = el("tr");
        [a.created_at, `${a.label || a.item}`, a.fy, fmtN(a.original, 2), fmtN(a.adjusted, 2), a.reason,
          a.affects_model ? "會套用（下次估值）" : "不會改變估值"].forEach((c) => tr.appendChild(el("td", null, c)));
        const del = el("td");
        const b = el("button", "btn tiny danger", "刪除");
        b.type = "button";
        b.addEventListener("click", () => deleteAdjustment(a.id));
        del.appendChild(b);
        tr.appendChild(del);
        tbody.appendChild(tr);
      });
      t.appendChild(tbody);
      list.appendChild(t);
    }
    body.appendChild(list);
    host.appendChild(body);
  }

  function openEditor(tr, stmt, row) {
    if (tr.nextSibling && tr.nextSibling.classList && tr.nextSibling.classList.contains("editor-row")) {
      tr.nextSibling.remove();
      return;
    }
    const er = el("tr", "editor-row");
    const td = el("td");
    td.colSpan = 4 + stmt.years.length;
    const form = el("div", "editor");
    const fySel = el("select", "input small");
    stmt.years.forEach((y) => {
      const o = el("option", null, y); o.value = y; fySel.appendChild(o);
    });
    const val = el("input", "input small num");
    val.type = "number"; val.step = "any";
    const reason = el("input", "input small");
    reason.type = "text"; reason.placeholder = "修改原因（必填，會寫入紀錄）";
    const hint = el("div", "editor-hint muted");
    const fill = () => {
      const v = row.values[fySel.value];
      val.value = v != null ? Number(v).toString() : "";
      hint.textContent = `原值 ${fmtN(v, 4)} ${row.unit}${row.model_input ? "；此科目是模型輸入，會影響下次估值" : "；此科目不是模型直接輸入，只會留痕"}`;
    };
    fySel.addEventListener("change", fill);
    fill();
    const save = el("button", "btn small primary", "儲存修改");
    save.type = "button";
    const cancel = el("button", "btn small", "取消");
    cancel.type = "button"; cancel.addEventListener("click", () => er.remove());
    const status = el("span", "editor-status");

    save.addEventListener("click", async () => {
      status.textContent = "儲存中…";
      status.className = "editor-status";
      try {
        const payload = {
          ticker: state.ticker, statement: stmt.key, item: row.item, fy: fySel.value,
          adjusted: Number(val.value), reason: reason.value.trim(),
        };
        const data = await window.App.api("/api/review/adjustments", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        });
        state.research.adjustments = data.adjustments;
        state.research.adopted = data.adjustments;
        renderReview();
        $("review-block").open = true;
        window.App.toast("已記錄修改。下次估值會套用並在 Excel「金額調整紀錄」留下痕跡。");
      } catch (err) {
        status.textContent = err.message || "儲存失敗";
        status.className = "editor-status bad";
      }
    });

    form.appendChild(el("strong", null, `修改：${row.label}`));
    const fields = el("div", "editor-fields");
    [["財報期", fySel], ["修改後金額／數值", val], ["原因", reason]].forEach(([label, node]) => {
      const f = el("label", "field");
      f.appendChild(el("span", null, label));
      f.appendChild(node);
      fields.appendChild(f);
    });
    form.appendChild(fields);
    form.appendChild(hint);
    const acts = el("div", "editor-actions");
    acts.appendChild(save); acts.appendChild(cancel); acts.appendChild(status);
    form.appendChild(acts);
    td.appendChild(form);
    er.appendChild(td);
    tr.parentNode.insertBefore(er, tr.nextSibling);
    val.focus();
  }

  async function deleteAdjustment(id) {
    try {
      const data = await window.App.api(
        `/api/review/adjustments/${encodeURIComponent(id)}?ticker=${encodeURIComponent(state.ticker)}`,
        { method: "DELETE" });
      state.research.adjustments = data.adjustments;
      renderReview();
      $("review-block").open = true;
      window.App.toast("已刪除這筆修改紀錄。");
    } catch (err) {
      window.App.toast(err.message || "刪除失敗", true);
    }
  }

  // ------------------------------------------------------------- forecast ---
  function renderForecast() {
    const host = $("forecast-block");
    if (!host) return;
    const fc = state.research?.forecast;
    host.innerHTML = "";
    host.appendChild(el("h3", null, "歷史財報與預測假設並列"));
    if (!fc || !fc.ok) {
      host.appendChild(el("p", "muted", fc?.error || "尚無可並列的預測。"));
      return;
    }
    const table = el("table", "forecast-table");
    const thead = el("thead");
    const hr = el("tr");
    ["年／期間", "類型", "營收", "增長", "EBIT", "EBIT 利潤率", "D&A", "Capex", "FCFF", "現值"].forEach((h, i) => {
      hr.appendChild(el("th", i >= 2 ? "num" : null, h));
    });
    thead.appendChild(hr); table.appendChild(thead);
    const tb = el("tbody");
    fc.rows.forEach((r) => {
      const tr = el("tr");
      tr.classList.add(`row-${r.kind}`);
      tr.appendChild(el("td", null, r.year || "—"));
      tr.appendChild(el("td", null, { actual: "實際", base: "基準年", forecast: "預測" }[r.kind] || ""));
      [["revenue", 1], ["growth", 3], ["ebit", 1], ["ebit_margin", 3], ["da", 1], ["capex", 1], ["fcff", 1], ["pv", 1]]
        .forEach(([k, d]) => {
          const isPct = k === "growth" || k === "ebit_margin";
          tr.appendChild(el("td", "num", isPct ? fmtPct(r[k]) : fmtN(r[k], d === 1 ? 1 : 2)));
        });
      tr.addEventListener("click", () => {
        const y = r.year;
        state.forecastOpen = state.forecastOpen === y ? null : y;
        renderReasons();
        const box = $("reasons-block");
        if (box) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    host.appendChild(table);
    host.appendChild(el("p", "hint", fc.note || ""));
    if (fc.units) host.appendChild(el("p", "hint", `單位：${fc.units}`));
    if (fc.applied_adjustments && fc.applied_adjustments.length) {
      const applied = el("div", "applied");
      applied.appendChild(el("strong", null, "本次估值已套用的財報調整："));
      fc.applied_adjustments.forEach((a) => {
        applied.appendChild(el("div", "applied-item",
          `${a.label || a.item}（${a.fy}）：${fmtN(a.original, 2)} → ${fmtN(a.adjusted, 2)}；${a.effect}；原因：${a.reason}`));
      });
      host.appendChild(applied);
    }
    renderReasons();
  }

  function renderReasons() {
    const host = $("reasons-block");
    if (!host) return;
    const fc = state.research?.forecast;
    host.innerHTML = "";
    host.appendChild(el("h3", null, "預測理由（逐年）"));
    if (!fc || !fc.reasons || !fc.reasons.length) {
      host.appendChild(el("p", "muted", "沒有預測理由可顯示。"));
      return;
    }
    const chosen = state.forecastOpen ? fc.reasons.filter((r) => r.year === state.forecastOpen) : fc.reasons;
    chosen.forEach((r) => {
      const card = el("div", "reason-card" + (state.forecastOpen ? " open" : ""));
      const head = el("div", "reason-head");
      head.appendChild(el("strong", null, r.year || "—"));
      if (r.growth != null) head.appendChild(el("span", "badge", `增長 ${fmtPct(r.growth)}`));
      card.appendChild(head);
      [["增長理由", r.growth_rationale], ["利潤率理由", r.margin_rationale], ["再投資理由", r.reinvestment_rationale]]
        .forEach(([label, text]) => {
          if (!text) return;
          const row = el("div", "reason-row");
          row.appendChild(el("span", "reason-label", label));
          row.appendChild(el("span", "reason-text", text));
          card.appendChild(row);
        });
      host.appendChild(card);
    });
    if (!state.forecastOpen) host.appendChild(el("p", "hint", "點上方表格任何一列，只看該期間的理由。"));

    // R21 traceability: every reason card names the figures it justifies.
    const trace = $("trace-block");
    if (trace) {
      trace.innerHTML = "";
      const term = state.research?.forecast;
      if (term && term.ok) {
        trace.appendChild(el("h3", null, "由理論價往回追溯"));
        const meta = state.meta || {};
        const chain = [
          ["理論價", `${currencyOf(meta.currency)}${fmtN(meta.fair_value_per_share, 2)}（模型輸出）`],
          ["預測", `${term.rows.filter((r) => r.kind === "forecast").length} 個預測期，見「歷史與預測並列」`],
          ["理由", "逐年增長／利潤率／再投資理由，見上一節"],
          ["歷史財報", `${term.rows.filter((r) => r.kind === "actual").length} 個已公布財年，原始科目見「財報逐項覆核」`],
          ["調整", (term.applied_adjustments || []).length
            ? `${term.applied_adjustments.length} 筆已套用（見「金額調整紀錄」）` : "本次沒有套用調整"],
        ];
        const ol = el("ol", "trace");
        chain.forEach(([k, v]) => {
          const li = el("li");
          li.appendChild(el("strong", null, k + "："));
          li.appendChild(el("span", null, v));
          ol.appendChild(li);
        });
        trace.appendChild(ol);
      }
    }
  }

  // ---------------------------------------------------------------- usage ---
  function renderUsage() {
    const host = $("usage-block");
    if (!host) return;
    const u = state.research?.usage || state.usage || {};
    host.innerHTML = "";
    host.appendChild(el("h3", null, "AI 用量（本次實際）"));
    if (!u.requests) {
      host.appendChild(el("p", "muted", "本次沒有呼叫 AI（未勾選使用，或未設定連線）。工作量與實際用量是兩件事：用量只在 AI 真的執行後才有數字。"));
      return;
    }
    const cards = el("div", "usage-cards");
    [["請求次數", u.requests, "次"], ["輸入 Token", u.prompt_tokens, ""],
      ["輸出 Token", u.completion_tokens, ""], ["總 Token", u.total_tokens, ""]]
      .forEach(([k, v, suffix]) => {
        const c = el("div", "usage-card");
        c.appendChild(el("div", "usage-k", k));
        c.appendChild(el("div", "usage-v", `${(v ?? 0).toLocaleString()}${suffix}`));
        cards.appendChild(c);
      });
    host.appendChild(cards);
    host.appendChild(el("p", "hint", u.note || ""));
    if (u.unreported_requests) {
      host.appendChild(el("p", "hint warn", `${u.unreported_requests} 次請求未獲服務商回報用量，未計入總數。`));
    }
    if ((u.events || []).length) {
      const t = el("table", "mini");
      const th = el("thead"), trh = el("tr");
      ["步驟", "模型", "輸入", "輸出", "總計"].forEach((h) => trh.appendChild(el("th", null, h)));
      th.appendChild(trh); t.appendChild(th);
      const tb = el("tbody");
      u.events.forEach((e) => {
        const tr = el("tr");
        [e.tag, e.model || "—", e.prompt_tokens ?? "—", e.completion_tokens ?? "—",
          e.total_tokens ?? (e.reported ? "—" : "未回報")].forEach((c) => tr.appendChild(el("td", null, String(c))));
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      host.appendChild(t);
    }
  }

  // -------------------------------------------------------------- AI plan ---
  function renderAiPlan(plan) {
    const host = $("ai-plan-body");
    if (!host) return;
    host.innerHTML = "";
    const intro = el("p", "muted",
      `以下是 ${plan.ticker} 這次估值中 AI 會做的工作（模型：${plan.model || "未設定"}）。數量按實際收集到的資料計算。`);
    host.appendChild(intro);
    const t = el("table", "mini");
    const th = el("thead"), trh = el("tr");
    ["AI 工作", "這次會執行？", "數量", "說明"].forEach((h) => trh.appendChild(el("th", null, h)));
    th.appendChild(trh); t.appendChild(th);
    const tb = el("tbody");
    plan.steps.forEach((s) => {
      const tr = el("tr");
      if (!s.will_run) tr.classList.add("skipped");
      tr.appendChild(el("td", null, s.label));
      tr.appendChild(el("td", null, s.will_run ? "會" : `不會（${s.skip_reason || "略過"}）`));
      tr.appendChild(el("td", null, s.will_run ? `${s.requests} 次／${s.items} 項` : "—"));
      tr.appendChild(el("td", null, s.detail));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    host.appendChild(t);
    host.appendChild(el("p", "hint",
      `合計 ${plan.totals.requests} 次請求、${plan.totals.items} 個項目；${plan.totals.skipped} 個步驟不執行。${plan.note || ""}`));
    const eta = state.eta;
    if (eta) {
      host.appendChild(el("p", "hint", eta.median_seconds
        ? `預估時間：${eta.note}（樣本 ${eta.sample_size} 次）。`
        : eta.note));
    }
  }

  // ------------------------------------------------------------- assembly ---
  function render(research, meta, usage) {
    state.research = research || {};
    state.meta = meta || {};
    state.usage = usage || {};
    state.ticker = (meta && meta.ticker) || (research && research.ticker) || state.ticker;
    state.chart.points = (research && research.history && research.history.points) || [];
    state.chart.selected = state.chart.points.length ? state.chart.points[state.chart.points.length - 1] : null;
    state.chart.compare = [];
    state.reviewStatement = state.reviewStatement || "is";
    state.reviewFilter = "";
    renderReview();
    renderChart();
    renderForecast();
    renderUsage();
    onTab(window.App.currentTabIndex ? window.App.currentTabIndex() : 0);
    const det = $("review-block");
    if (det) det.open = false;
  }

  const TAB_TARGETS = {
    first: ["chart-block"],
    last: ["usage-block", "forecast-block", "reasons-block", "trace-block"],
  };

  function onTab(index) {
    const blocks = document.querySelectorAll("[data-tab-only]");
    blocks.forEach((b) => {
      const want = b.dataset.tabOnly;
      const isFirst = index === 0;
      const isLast = index === (window.App.sheetCount() - 1);
      const show = (want === "first" && isFirst) || (want === "last" && isLast);
      b.classList.toggle("hidden", !show);
    });
    if (index === (window.App.sheetCount() - 1)) {
      renderForecast();
      renderUsage();
    }
  }

  window.Research = {
    render, onTab, renderChart, renderReview, renderForecast, renderUsage, renderAiPlan,
    setEta: (eta) => { state.eta = eta; },
    state,
  };
})();
