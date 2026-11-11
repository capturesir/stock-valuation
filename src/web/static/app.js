(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    form: $("lookup-form"),
    ticker: $("ticker"),
    lookupErr: $("lookup-error"),
    card: $("quote-card"),
    name: $("q-name"),
    meta: $("q-meta"),
    price: $("q-price"),
    priceSub: $("q-price-sub"),
    facts: $("q-facts"),
    specNote: $("spec-note"),
    lastVal: $("last-val"),
    lastValText: $("last-val-text"),
    btnViewLast: $("btn-view-last"),
    btnDlLast: $("btn-dl-last"),
    valDate: $("val-date"),
    noLlm: $("no-llm"),
    btnValuate: $("btn-valuate"),
    btnCheck: $("btn-check"),
    rerun: $("rerun-if-needed"),
    statusPanel: $("status-panel"),
    progress: $("progress"),
    runError: $("run-error"),
    filingsBox: $("filings-box"),
    filingsStatus: $("filings-status"),
    filingsJson: $("filings-json"),
    resultPanel: $("result-panel"),
    resultSummary: $("result-summary"),
    btnDownload: $("btn-download"),
    btnDlSignals: $("btn-dl-signals"),
    btnExportSignals: $("btn-export-signals"),
    signalsMsg: $("signals-msg"),
    tabs: $("sheet-tabs"),
    sheetView: $("sheet-view"),
    sheetNote: $("sheet-note"),
  };

  let currentTicker = null;
  let lastLookup = null;
  let pollTimer = null;
  let tickTimer = null;
  let currentWorkbook = null;
  let currentMeta = null;
  let currentSignalsDl = null;

  function todayISO() {
    const n = new Date();
    const y = n.getFullYear();
    const m = String(n.getMonth() + 1).padStart(2, "0");
    const d = String(n.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  els.valDate.value = todayISO();

  function show(el) { el.classList.remove("hidden"); }
  function hide(el) { el.classList.add("hidden"); }

  function setError(el, msg) {
    if (!msg) { hide(el); el.textContent = ""; return; }
    el.textContent = msg;
    show(el);
  }

  function fmtNum(v, digits) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    return n.toLocaleString("zh-Hant", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function fmtPct(v) {
    if (v == null || v === "") return "—";
    return `${(Number(v) * 100).toFixed(2)}%`;
  }

  function fmtMult(v) {
    if (v == null || v === "") return "—";
    return `${Number(v).toFixed(2)}×`;
  }

  function currencyOf(code) {
    const map = { USD: "US$", HKD: "HK$", TWD: "NT$", CNY: "RMB", JPY: "¥", EUR: "€", GBP: "£" };
    return map[code] || (code ? `${code} ` : "");
  }

  function fmtCap(v, ccy) {
    if (v == null) return "—";
    const n = Number(v);
    const p = currencyOf(ccy);
    if (n >= 1e12) return `${p}${(n / 1e12).toFixed(2)} 兆`;
    if (n >= 1e8) return `${p}${(n / 1e8).toFixed(2)} 億`;
    return `${p}${fmtNum(n, 0)}`;
  }

  async function api(url, opts) {
    const res = await fetch(url, opts);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!res.ok) {
      const detail = data && (data.detail || data);
      const msg = (detail && (detail.error || detail.message)) || (typeof detail === "string" ? detail : null) || text || `HTTP ${res.status}`;
      const err = new Error(msg);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function clearRunState() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
    setError(els.runError, "");
    hide(els.progress);
    els.progress.textContent = "";
  }

  els.form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const ticker = els.ticker.value.trim();
    if (!ticker) return;
    els.btnLookup = $("btn-lookup");
    $("btn-lookup").disabled = true;
    setError(els.lookupErr, "");
    hide(els.card);
    hide(els.resultPanel);
    hide(els.filingsBox);
    clearRunState();
    hide(els.statusPanel);
    currentTicker = null;
    lastLookup = null;
    try {
      const data = await api(`/api/lookup?ticker=${encodeURIComponent(ticker)}`);
      lastLookup = data;
      currentTicker = data.ticker;
      renderQuote(data);
      show(els.card);
    } catch (err) {
      hide(els.card);
      currentTicker = null;
      const msg = err.data && err.data.error ? err.data.error : err.message;
      setError(els.lookupErr, msg || "查詢失敗。");
    } finally {
      $("btn-lookup").disabled = false;
    }
  });

  function renderQuote(data) {
    const q = data.quote || {};
    const name = data.display_name || q.long_name || q.short_name || data.ticker;
    els.name.textContent = name;
    const bits = [data.ticker];
    if (q.exchange) bits.push(q.exchange);
    if (q.instrument_type) bits.push(q.instrument_type);
    els.meta.textContent = bits.join(" · ");
    const ccy = q.currency || "";
    els.price.textContent = `${currencyOf(ccy)}${fmtNum(q.price, 2)}`;
    els.priceSub.textContent = [ccy, q.price_date ? `市價日期 ${q.price_date}` : ""].filter(Boolean).join(" · ");
    const facts = [
      ["代號", data.ticker],
      ["貨幣", ccy || "—"],
      ["交易所", q.exchange || "—"],
      ["市價日期", q.price_date || "—"],
    ];
    if (q.sector) facts.push(["行業", q.sector]);
    if (q.industry) facts.push(["產業", q.industry]);
    if (q.market_cap != null) facts.push(["市值", fmtCap(q.market_cap, ccy)]);
    if (q.week52_low != null && q.week52_high != null) {
      facts.push(["52 週", `${currencyOf(ccy)}${fmtNum(q.week52_low, 2)} – ${fmtNum(q.week52_high, 2)}`]);
    }
    els.facts.innerHTML = "";
    for (const [k, v] of facts) {
      const wrap = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = k;
      if (k === "代號" && q.price_url) {
        const a = document.createElement("a");
        a.href = q.price_url;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = v;
        dd.appendChild(a);
      } else {
        dd.textContent = v;
      }
      wrap.appendChild(dt);
      wrap.appendChild(dd);
      els.facts.appendChild(wrap);
    }
    if (data.has_name_spec) {
      hide(els.specNote);
      els.specNote.textContent = "";
    } else {
      els.specNote.textContent = "本封裝驗證版內建規格僅含 NVDA 與 1830.HK。此代號可顯示市價，但點擊「估值」會失敗，直至新增 name_specs 並備妥來源。";
      show(els.specNote);
    }
    if (data.last_valuation && data.last_valuation.xlsx) {
      const lv = data.last_valuation;
      const verify = lv.verify_pass == null ? "" : (lv.verify_pass ? "驗證通過" : "驗證失敗");
      const parts = [
        `估值日 ${lv.valuation_date}`,
        lv.fair_value_per_share != null ? `基準理論價 ${fmtNum(lv.fair_value_per_share, 2)}` : null,
        lv.price != null ? `當時市價 ${fmtNum(lv.price, 2)}` : null,
        verify,
        lv.assumption_source ? `假設來源 ${lv.assumption_source}` : null,
      ].filter(Boolean);
      els.lastValText.textContent = parts.join(" · ") + "。查詢不會自動重跑；可先檢視上次結果，或按「估值」重新計算。";
      els.btnDlLast.href = `/api/workbook/excel?ticker=${encodeURIComponent(data.ticker)}&date=${encodeURIComponent(lv.valuation_date)}`;
      els.btnDlLast.setAttribute("download", `${lv.valuation_date}_${data.ticker}_FCFF.xlsx`);
      show(els.lastVal);
    } else {
      hide(els.lastVal);
    }
  }

  els.btnViewLast.addEventListener("click", async () => {
    if (!currentTicker) return;
    const date = lastLookup && lastLookup.last_valuation && lastLookup.last_valuation.valuation_date;
    await loadWorkbook(currentTicker, date, lastLookup && lastLookup.last_valuation);
  });

  els.btnValuate.addEventListener("click", async () => {
    if (!currentTicker) return;
    await startValuation(currentTicker);
  });

  els.btnCheck.addEventListener("click", async () => {
    if (!currentTicker) return;
    await checkFilings(currentTicker);
  });

  async function startValuation(ticker) {
    clearRunState();
    show(els.statusPanel);
    hide(els.filingsBox);
    setError(els.runError, "");
    els.btnValuate.disabled = true;
    const body = {
      ticker,
      date: els.valDate.value || null,
      no_llm: !!els.noLlm.checked,
    };
    try {
      const data = await api("/api/valuate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      watchJob(data.job);
    } catch (err) {
      setError(els.runError, err.message || "無法開始估值。");
      els.btnValuate.disabled = false;
    }
  }

  function watchJob(job) {
    const started = (job.started_at ? job.started_at * 1000 : Date.now());
    const updateProgress = (j) => {
      const sec = Math.max(0, Math.round((Date.now() - started) / 1000));
      const st = j && j.status ? j.status : "running";
      const msg = (j && j.message) || "估值進行中…";
      els.progress.textContent = `${msg}\n狀態：${st}　已過 ${sec} 秒。請稍候，不要關閉此頁。`;
      show(els.progress);
    };
    updateProgress(job);
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = setInterval(() => updateProgress(job), 1000);

    const finish = async (j) => {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
      els.btnValuate.disabled = false;
      job = j;
      updateProgress(j);
      if (j.status === "error") {
        hide(els.progress);
        setError(els.runError, j.error || j.message || "估值失敗。");
        return;
      }
      if (j.status === "done") {
        const meta = j.result || {};
        if (meta.verify_pass === false) {
          const errs = (meta.verify_errors || []).join("；") || "獨立驗證未通過";
          setError(els.runError, `估值已產出，但驗證失敗：${errs}`);
        }
        try {
          const data = await api(`/api/jobs/${encodeURIComponent(j.id)}/sheets`);
          showWorkbook(data.workbook, data.meta, `/api/jobs/${encodeURIComponent(j.id)}/download`);
        } catch (err) {
          setError(els.runError, (els.runError.textContent ? els.runError.textContent + "\n" : "") + (err.message || "無法載入 Excel 分頁。"));
        }
      }
    };

    const poll = async () => {
      try {
        const data = await api(`/api/jobs/${encodeURIComponent(job.id)}`);
        job = data.job;
        updateProgress(job);
        if (job.status === "done" || job.status === "error") {
          await finish(job);
        }
      } catch (err) {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
        els.btnValuate.disabled = false;
        setError(els.runError, err.message || "無法取得估值進度。");
      }
    };
    pollTimer = setInterval(poll, 1000);
    poll();
  }

  async function checkFilings(ticker) {
    show(els.statusPanel);
    setError(els.runError, "");
    els.btnCheck.disabled = true;
    try {
      const data = await api("/api/check-filings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker,
          rerun_if_needed: !!els.rerun.checked,
          date: els.valDate.value || null,
        }),
      });
      renderFilings(data);
      if (data.job) {
        watchJob(data.job);
      }
    } catch (err) {
      const data = err.data;
      if (data && data.status && data.result) {
        renderFilings(data);
        setError(els.runError, err.message);
      } else {
        setError(els.runError, err.message || "財報檢查失敗。");
      }
    } finally {
      els.btnCheck.disabled = false;
    }
  }

  function renderFilings(data) {
    show(els.statusPanel);
    show(els.filingsBox);
    const st = data.status || "—";
    els.filingsStatus.textContent = st;
    els.filingsStatus.className = "badge " + (
      st === "NO_RERUN" ? "ok" : st === "RERUN_NEEDED" ? "warn" : "bad"
    );
    els.filingsJson.textContent = JSON.stringify(data.result || data, null, 2);
    if (data.message) {
      els.progress.textContent = data.message;
      show(els.progress);
    }
  }

  async function loadWorkbook(ticker, date, meta) {
    setError(els.runError, "");
    show(els.statusPanel);
    try {
      let url = `/api/workbook?ticker=${encodeURIComponent(ticker)}`;
      if (date) url += `&date=${encodeURIComponent(date)}`;
      const data = await api(url);
      let dl = `/api/workbook/excel?ticker=${encodeURIComponent(ticker)}`;
      const usedDate = (data.meta && data.meta.valuation_date) || date;
      if (usedDate) dl += `&date=${encodeURIComponent(usedDate)}`;
      showWorkbook(data.workbook, data.meta || meta || {}, dl);
    } catch (err) {
      setError(els.runError, err.message || "無法載入上次估值。");
    }
  }


  function signalsUrl(meta, downloadUrl) {
    const ticker = meta && meta.ticker;
    const date = meta && meta.valuation_date;
    if (downloadUrl && /\/api\/jobs\/[^/]+\/download/.test(downloadUrl)) {
      return downloadUrl.replace(/\/download(?:\?.*)?$/, "/signals");
    }
    if (!ticker) return null;
    let url = `/api/workbook/signals?ticker=${encodeURIComponent(ticker)}`;
    if (date) url += `&date=${encodeURIComponent(date)}`;
    return url;
  }


  if (els.btnExportSignals) {
    els.btnExportSignals.addEventListener("click", async () => {
      const meta = currentMeta || {};
      const ticker = meta.ticker;
      if (!ticker) return;
      els.btnExportSignals.disabled = true;
      if (els.signalsMsg) {
        els.signalsMsg.textContent = "正在匯出訊號…";
        els.signalsMsg.classList.remove("hidden");
      }
      try {
        const data = await api("/api/export-signals", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker, date: meta.valuation_date || null }),
        });
        const lines = (data.summary || []).join("\n");
        const dest = data.dest || "out/signals.json";
        const note = data.disclaimer || "此為研究訊號檔，並非投資建議或下單指令。";
        if (els.signalsMsg) {
          els.signalsMsg.textContent = `已寫入 ${dest}` + (lines ? "\n" + lines : "") + "\n" + note;
        }
      } catch (err) {
        if (els.signalsMsg) {
          els.signalsMsg.textContent = err.message || "匯出訊號失敗。";
        }
      } finally {
        els.btnExportSignals.disabled = false;
      }
    });
  }

  function showWorkbook(workbook, meta, downloadUrl) {
    currentWorkbook = workbook;
    show(els.resultPanel);
    const title = [meta.asset || meta.company, meta.ticker].filter(Boolean).join(" ");
    const lines = [];
    if (title) lines.push(title);
    const bits = [];
    if (meta.valuation_date) bits.push(`估值日 ${meta.valuation_date}`);
    if (meta.price != null) bits.push(`市價 ${fmtNum(meta.price, 2)}${meta.currency ? " " + meta.currency : ""}`);
    if (meta.price_date) bits.push(`市價日期 ${meta.price_date}`);
    if (meta.fair_value_per_share != null) bits.push(`基準理論價 ${fmtNum(meta.fair_value_per_share, 2)}`);
    if (meta.price_over_fair != null) bits.push(`市價／基準 ${fmtMult(meta.price_over_fair)}`);
    if (meta.wacc != null) bits.push(`WACC ${fmtPct(meta.wacc)}`);
    if (meta.g != null) bits.push(`終值 g ${fmtPct(meta.g)}`);
    if (meta.verify_pass != null) bits.push(meta.verify_pass ? "驗證通過" : "驗證失敗");
    if (meta.assumption_source) bits.push(`假設來源 ${meta.assumption_source}`);
    els.resultSummary.innerHTML = "";
    const h = document.createElement("h2");
    h.textContent = title || "估值結果";
    els.resultSummary.appendChild(h);
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = bits.join(" · ");
    els.resultSummary.appendChild(p);
    if (meta.verify_pass === false && meta.verify_errors && meta.verify_errors.length) {
      const e = document.createElement("p");
      e.className = "banner error";
      e.style.margin = "0.4rem 0 0";
      e.textContent = "驗證錯誤：" + meta.verify_errors.join("；");
      els.resultSummary.appendChild(e);
    }
    currentMeta = meta || {};
    if (downloadUrl) {
      els.btnDownload.href = downloadUrl;
      const fname = (meta.xlsx && String(meta.xlsx).split(/[\\/]/).pop()) || "valuation.xlsx";
      els.btnDownload.setAttribute("download", fname);
      show(els.btnDownload);
    }
    const sigUrl = signalsUrl(meta, downloadUrl);
    currentSignalsDl = sigUrl;
    if (sigUrl && els.btnDlSignals) {
      els.btnDlSignals.href = sigUrl;
      const ticker = (meta && meta.ticker) || "ticker";
      const vdate = (meta && meta.valuation_date) || "";
      const idDate = vdate ? vdate.replaceAll("-", "") : "";
      els.btnDlSignals.setAttribute("download", (idDate ? idDate + "-" + ticker : ticker) + "_signals.json");
      show(els.btnDlSignals);
    } else if (els.btnDlSignals) {
      hide(els.btnDlSignals);
    }
    if (els.btnExportSignals) {
      if (meta && meta.ticker) show(els.btnExportSignals);
      else hide(els.btnExportSignals);
    }
    if (els.signalsMsg) {
      els.signalsMsg.textContent = "";
      hide(els.signalsMsg);
    }
    const warns = (workbook && workbook.warnings) || [];
    els.sheetNote.textContent = warns.length
      ? warns.join(" ")
      : "下列分頁對應 Excel 各工作表。公式儲存格若無快取值，已在本機依同一公式重算顯示。";
    renderTabs(workbook);
    if (workbook && workbook.sheets && workbook.sheets.length) {
      showSheet(workbook, 0);
    }
    els.resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderTabs(workbook) {
    els.tabs.innerHTML = "";
    (workbook.sheets || []).forEach((sheet, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = sheet.name;
      b.setAttribute("role", "tab");
      if (i === 0) b.classList.add("active");
      b.addEventListener("click", () => {
        for (const x of els.tabs.querySelectorAll("button")) x.classList.remove("active");
        b.classList.add("active");
        showSheet(workbook, i);
      });
      els.tabs.appendChild(b);
    });
  }

  function showSheet(workbook, index) {
    const sheet = workbook.sheets[index];
    els.sheetView.innerHTML = "";
    const table = document.createElement("table");
    table.className = "sheet";
    const cg = document.createElement("colgroup");
    (sheet.col_widths || []).forEach((w) => {
      const col = document.createElement("col");
      col.style.minWidth = Math.max(48, Number(w) * 7.2) + "px";
      cg.appendChild(col);
    });
    table.appendChild(cg);
    (sheet.rows || []).forEach((row) => {
      const tr = document.createElement("tr");
      row.forEach((cell) => {
        const td = document.createElement("td");
        if (cell.rs) td.rowSpan = cell.rs;
        if (cell.cs) td.colSpan = cell.cs;
        if (cell.bg) td.style.background = cell.bg;
        if (cell.fg) td.style.color = cell.fg;
        if (cell.b) td.style.fontWeight = "700";
        if (cell.i) td.style.fontStyle = "italic";
        if (cell.a) td.style.textAlign = cell.a;
        if (cell.w) td.style.whiteSpace = "pre-wrap";
        if (cell.f) td.title = cell.f;
        if (cell.t === "err") td.classList.add("err");
        if (cell.h) {
          const a = document.createElement("a");
          a.href = cell.h;
          a.target = "_blank";
          a.rel = "noopener";
          a.textContent = cell.d || cell.h;
          td.appendChild(a);
        } else {
          td.textContent = cell.d || "";
        }
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    els.sheetView.appendChild(table);
  }
})();
