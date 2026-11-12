"""Web API: lookup, valuation job wiring, filings, download, and error display."""
from __future__ import annotations

import time
from pathlib import Path
from src.web import jobs as jobmod
from src.web.sheets import EXPECTED_SHEETS

ROOT = Path(__file__).resolve().parents[1]


def _chart(ticker, currency, name, exchange, price, date, **_k):
    return {
        "ok": True,
        "meta": {
            "currency": currency,
            "exchange": exchange,
            "symbol": ticker,
            "shortName": name,
            "instrumentType": "EQUITY",
        },
        "last": {"date": date, "close": price},
    }


def _nvda_chart(*a, **k):
    return _chart("NVDA", "USD", "NVIDIA Corporation", "NasdaqGS", 168.89, "2026-11-10")


def _1830_chart(*a, **k):
    return _chart("1830.HK", "HKD", "PERFECT MEDICAL", "HKSE", 1.49, "2026-11-10")


def _fail_chart(*a, **k):
    return {"ok": False, "error": "Yahoo chart 無結果", "http_status": 404}


def _patch_lookup(monkeypatch, chart_fn, extra=None):
    monkeypatch.setattr("src.fetch.yahoo.chart_quote", chart_fn)
    monkeypatch.setattr("src.web.services._enrich_profile", lambda t: extra or {})


def test_health_and_index(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "不構成投資建議" in r.json()["disclaimer"]
    page = client.get("/")
    assert page.status_code == 200
    text = page.text
    assert "股票代號" in text
    assert "估值" in text
    assert "本系統產出不構成投資建議" in text
    assert "檢查財報" in text


def test_health_reports_configured_llm_without_leaking_key(client, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "example-model")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
    body = client.get("/api/health").json()
    llm = body["llm"]
    assert llm["configured"] is True
    assert llm["model"] == "example-model"
    assert llm["base_url"] == "https://api.example.com/v1"
    assert llm["api_key_set"] is True
    assert "hint" not in llm
    assert "sk-test-not-a-real-key" not in str(body)


def test_health_reports_missing_llm_config(client, monkeypatch):
    for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    llm = client.get("/api/health").json()["llm"]
    assert llm["configured"] is False
    assert llm["hint"]


def test_llm_status_strips_query_string_from_base_url(monkeypatch):
    from src.web.services import llm_status

    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.example.com/v1?api-key=secret")
    monkeypatch.setenv("LLM_MODEL", "example-model")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
    status = llm_status()
    assert status["base_url"] == "https://gateway.example.com/v1"
    assert "secret" not in str(status)


def test_lookup_nvda_success(client, monkeypatch):
    _patch_lookup(monkeypatch, _nvda_chart, {"long_name": "NVIDIA Corporation", "sector": "Technology"})
    r = client.get("/api/lookup?ticker=nvda")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["ticker"] == "NVDA"
    assert data["has_name_spec"] is True
    assert data["quote"]["price"] == 168.89
    assert data["quote"]["price_date"] == "2026-11-10"
    assert data["quote"]["currency"] == "USD"
    assert "NVIDIA" in (data["display_name"] or "")
    assert data["last_valuation"]
    assert data["last_valuation"]["xlsx"]
    assert "NVDA_FCFF.xlsx" in data["last_valuation"]["xlsx"]


def test_lookup_1830_success(client, monkeypatch):
    _patch_lookup(monkeypatch, _1830_chart)
    r = client.get("/api/lookup?ticker=1830")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["ticker"] == "1830.HK"
    assert data["has_name_spec"] is True
    assert data["quote"]["price"] == 1.49
    assert data["quote"]["currency"] == "HKD"
    assert data["last_valuation"]["xlsx"]


def test_lookup_nonsense_ticker_error(client, monkeypatch):
    _patch_lookup(monkeypatch, _fail_chart)
    r = client.get("/api/lookup?ticker=NOTAREALTICKER999")
    assert r.status_code == 404
    data = r.json()
    assert data["ok"] is False
    assert "找不到" in data["error"] or "無法取得" in data["error"]
    assert "quote" not in data or not data.get("quote")


def test_lookup_does_not_start_valuation(client, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append((a, k))
        raise AssertionError("lookup 不應呼叫 run_ticker")

    monkeypatch.setattr("src.web.jobs.run_ticker", boom)
    _patch_lookup(monkeypatch, _nvda_chart)
    r = client.get("/api/lookup?ticker=NVDA")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert calls == []
    assert jobmod.store.active() is None
    assert jobmod.store.jobs == {}


def test_workbook_sheets_and_download_nvda(client):
    r = client.get("/api/workbook?ticker=NVDA&date=2026-11-11")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert [s["name"] for s in data["workbook"]["sheets"]] == EXPECTED_SHEETS
    for s in data["workbook"]["sheets"]:
        assert s["nonempty"] > 0
    blob = " ".join(c["d"] for s in data["workbook"]["sheets"] if s["name"] == "摘要" for row in s["rows"] for c in row)
    assert "NVIDIA" in blob and "874.98" in blob
    dl = client.get("/api/workbook/excel?ticker=NVDA&date=2026-11-11")
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"
    disp = dl.headers.get("content-disposition", "")
    assert "2026-11-11_NVDA_FCFF.xlsx" in disp


def test_workbook_sheets_and_download_1830(client):
    r = client.get("/api/workbook?ticker=1830.HK&date=2026-11-11")
    assert r.status_code == 200
    data = r.json()
    assert [s["name"] for s in data["workbook"]["sheets"]] == EXPECTED_SHEETS
    for s in data["workbook"]["sheets"]:
        assert s["nonempty"] > 0
    blob = " ".join(c["d"] for s in data["workbook"]["sheets"] if s["name"] == "摘要" for row in s["rows"] for c in row)
    assert "完美醫療" in blob and "5.51" in blob
    dl = client.get("/api/workbook/excel?ticker=1830.HK")
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"
    assert "1830.HK_FCFF.xlsx" in dl.headers.get("content-disposition", "")


def _wait_job(client, job_id, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        job = r.json()["job"]
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job 超時未結束")


def test_valuate_wired_to_run_ticker_and_missing_spec_error(client, monkeypatch):
    seen = {}

    def fake_run(ticker, cfg=None, use_llm=True, valuation_date="2026-11-11", progress=None, should_stop=None):
        seen["ticker"] = ticker
        seen["use_llm"] = use_llm
        seen["valuation_date"] = valuation_date
        raise RuntimeError("本封裝驗證版內建規格僅含 NVDA 與 1830.HK。其他代號請先新增 name_specs 並備妥來源，不可讓模型憑記憶填數。")

    monkeypatch.setattr("src.web.jobs.run_ticker", fake_run)
    r = client.post("/api/valuate", json={"ticker": "AAPL", "date": "2026-11-11", "no_llm": True})
    assert r.status_code == 200
    job_id = r.json()["job"]["id"]
    job = _wait_job(client, job_id)
    assert seen["ticker"] == "AAPL"
    assert seen["use_llm"] is False
    assert seen["valuation_date"] == "2026-11-11"
    assert job["status"] == "error"
    assert "name_specs" in job["error"]
    assert "NVDA" in job["error"]


def test_valuate_nonsense_ticker_error_path(client, monkeypatch):
    def fake_run(ticker, cfg=None, use_llm=True, valuation_date="2026-11-11", progress=None, should_stop=None):
        raise RuntimeError(f"無法取得 {ticker} 市價：Yahoo chart 無結果")

    monkeypatch.setattr("src.web.jobs.run_ticker", fake_run)
    r = client.post("/api/valuate", json={"ticker": "ZZZZZNONE"})
    job = _wait_job(client, r.json()["job"]["id"])
    assert job["status"] == "error"
    assert "無法取得" in job["error"]


def test_valuate_success_uses_existing_workbook(client, monkeypatch):
    xlsx = ROOT / "out" / "2026-11-11_NVDA" / "2026-11-11_NVDA_FCFF.xlsx"
    js = ROOT / "out" / "2026-11-11_NVDA" / "2026-11-11_NVDA.json"

    def fake_run(ticker, cfg=None, use_llm=True, valuation_date="2026-11-11", progress=None, should_stop=None):
        import json
        payload = json.loads(js.read_text(encoding="utf-8"))
        return {
            "json": payload,
            "path": str(js),
            "xlsx": str(xlsx),
            "md": str(xlsx).replace("_FCFF.xlsx", "_valuation.md"),
            "out_dir": str(xlsx.parent),
            "verify_pass": True,
            "verify_errors": [],
        }

    monkeypatch.setattr("src.web.jobs.run_ticker", fake_run)
    r = client.post("/api/valuate", json={"ticker": "NVDA", "date": "2026-11-11", "no_llm": True})
    job = _wait_job(client, r.json()["job"]["id"])
    assert job["status"] == "done"
    assert job["result"]["verify_pass"] is True
    assert job["result"]["ticker"] == "NVDA"
    sheets = client.get(f"/api/jobs/{job['id']}/sheets")
    assert sheets.status_code == 200
    names = [s["name"] for s in sheets.json()["workbook"]["sheets"]]
    assert names == EXPECTED_SHEETS
    dl = client.get(f"/api/jobs/{job['id']}/download")
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"


def test_second_concurrent_valuation_rejected(client, monkeypatch):
    import threading
    started = threading.Event()
    release = threading.Event()

    def fake_run(ticker, cfg=None, use_llm=True, valuation_date="2026-11-11", progress=None, should_stop=None):
        started.set()
        release.wait(timeout=3)
        raise RuntimeError("stopped")

    monkeypatch.setattr("src.web.jobs.run_ticker", fake_run)
    r1 = client.post("/api/valuate", json={"ticker": "NVDA"})
    assert r1.status_code == 200
    assert started.wait(timeout=2)
    r2 = client.post("/api/valuate", json={"ticker": "1830.HK"})
    assert r2.status_code == 409
    assert "進行中" in r2.json()["error"]
    release.set()
    _wait_job(client, r1.json()["job"]["id"])


def test_check_filings_route_statuses(client, monkeypatch):
    monkeypatch.setattr(
        "src.web.app.check_filings",
        lambda t: {"ticker": t, "rerun_needed": False, "latest_filing": {"form": "10-Q"}},
    )
    r = client.post("/api/check-filings", json={"ticker": "NVDA"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "NO_RERUN"
    assert data["job"] is None
    assert data["result"]["latest_filing"]["form"] == "10-Q"

    monkeypatch.setattr(
        "src.web.app.check_filings",
        lambda t: {"ticker": t, "rerun_needed": True, "reason": "new 10-Q"},
    )
    r = client.post("/api/check-filings", json={"ticker": "NVDA"})
    assert r.json()["status"] == "RERUN_NEEDED"
    assert r.json()["job"] is None  # 未勾選如需要則重跑

    monkeypatch.setattr(
        "src.web.app.check_filings",
        lambda t: {"ticker": t, "error": "被攔截", "blocked": True, "rerun_needed": False},
    )
    r = client.post("/api/check-filings", json={"ticker": "1830.HK"})
    assert r.json()["status"] == "CHECK_BLOCKED_OR_ERROR"


def test_check_filings_rerun_if_needed_starts_job(client, monkeypatch):
    monkeypatch.setattr(
        "src.web.app.check_filings",
        lambda t: {"ticker": t, "rerun_needed": True, "reason": "new filing"},
    )
    called = {}

    def fake_run(ticker, cfg=None, use_llm=True, valuation_date="2026-11-11", progress=None, should_stop=None):
        called["ticker"] = ticker
        raise RuntimeError("wiring only")

    monkeypatch.setattr("src.web.jobs.run_ticker", fake_run)
    r = client.post("/api/check-filings", json={"ticker": "NVDA", "rerun_if_needed": True, "date": "2026-11-11"})
    assert r.status_code == 200
    assert r.json()["status"] == "RERUN_NEEDED"
    assert r.json()["job"]
    job = _wait_job(client, r.json()["job"]["id"])
    assert called["ticker"] == "NVDA"
    assert job["status"] == "error"
    assert "wiring only" in job["error"]


def _wait_job_done(client, job_id, timeout=5.0):
    import time as _t
    start = _t.time()
    job = {}
    while _t.time() - start < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()["job"]
        if job.get("status") in ("done", "error"):
            return job
        _t.sleep(0.05)
    return job


def test_valuate_job_publishes_steps_and_marks_failure(client, monkeypatch):
    from src.web import jobs as jobmod

    def fake_run(ticker, cfg=None, use_llm=True, valuation_date="2026-11-11", progress=None, should_stop=None):
        progress("quote", "取得市價")
        progress("fundamentals", "讀取財報")
        progress("engine", "計算五情境")
        raise RuntimeError("模擬失敗")

    monkeypatch.setattr(jobmod, "run_ticker", fake_run)
    r = client.post("/api/valuate", json={"ticker": "TEST", "no_llm": True})
    assert r.status_code == 200
    job = _wait_job_done(client, r.json()["job"]["id"])

    keys = [s["key"] for s in job["steps"]]
    assert keys[:4] == ["quote", "fundamentals", "rf", "filings"]
    assert len(keys) == 10
    assert job["status"] == "error"
    assert "模擬失敗" in job["error"]
    # the step that was running when it blew up is flagged, earlier ones are done
    assert [s["status"] for s in job["steps"]][:2] == ["done", "done"]
    assert any(s["status"] == "error" for s in job["steps"])
    assert isinstance(job["progress"], int)


def test_valuate_job_finishes_at_full_progress(client, monkeypatch):
    from src.web import jobs as jobmod

    def fake_run(ticker, cfg=None, use_llm=True, valuation_date="2026-11-11", progress=None, should_stop=None):
        for key in [k for k, _ in jobmod.STEPS]:
            progress(key, f"做 {key}")
        return {
            "json": {
                "ticker": ticker, "asset": "Test Co", "price": 10.0, "price_date": "2026-11-11",
                "currency": "USD", "fair_value_per_share": 12.0,
                "gap_vs_price": {}, "discount": {"wacc": {"value": 0.09}},
                "terminal": {"g": 0.03}, "scenarios": [],
                "assumption_source": "rules_fallback",
            },
            "verify_pass": True, "verify_errors": [], "xlsx": "", "md": "", "path": "",
            "out_dir": "",
        }

    monkeypatch.setattr(jobmod, "run_ticker", fake_run)
    r = client.post("/api/valuate", json={"ticker": "TEST", "no_llm": True})
    job = _wait_job_done(client, r.json()["job"]["id"])

    assert job["status"] == "done"
    assert job["progress"] == 100
    assert job["activity"] == "完成"
    assert all(s["status"] in ("done", "skipped") for s in job["steps"])


def test_cancel_stops_a_running_valuation(client, monkeypatch, tmp_path):
    from src.web import jobs as jobmod
    from src.pipeline.orchestrator import ValuationCancelled

    monkeypatch.setattr(jobmod, "HISTORY_PATH", tmp_path / "hist.json")

    def fake_run(ticker, cfg=None, use_llm=True, valuation_date="2026-11-11",
                 progress=None, should_stop=None):
        progress("quote", "取得市價")
        for _ in range(200):
            if should_stop and should_stop():
                raise ValuationCancelled("用戶已停止估值")
            time.sleep(0.01)
        return {}

    monkeypatch.setattr(jobmod, "run_ticker", fake_run)
    r = client.post("/api/valuate", json={"ticker": "TEST", "no_llm": True})
    job_id = r.json()["job"]["id"]

    c = client.post(f"/api/jobs/{job_id}/cancel")
    assert c.status_code == 200
    assert c.json()["job"]["status"] in ("cancelling", "cancelled")

    job = _wait_job_done(client, job_id)
    assert job["status"] == "cancelled"
    assert job["error"] is None
    assert any(s["status"] == "cancelled" for s in job["steps"])
    assert job["cancellable"] is False


def test_error_history_keeps_last_three_runs(client, monkeypatch, tmp_path):
    from src.web import jobs as jobmod

    monkeypatch.setattr(jobmod, "HISTORY_PATH", tmp_path / "hist.json")

    def fake_run(ticker, cfg=None, use_llm=True, valuation_date="2026-11-11",
                 progress=None, should_stop=None):
        progress("quote", "取得市價")
        progress("engine", "計算五情境")
        raise RuntimeError(f"{ticker} 模擬失敗")

    monkeypatch.setattr(jobmod, "run_ticker", fake_run)
    for t in ("AAA", "BBB", "CCC", "DDD"):
        client.post("/api/valuate", json={"ticker": t, "no_llm": True})
        _wait_job_done(client, jobmod.store.active_id or "")
        # wait until the store frees up for the next run
        for _ in range(100):
            if jobmod.store.active() is None:
                break
            time.sleep(0.02)

    runs = client.get("/api/errors").json()["runs"]
    assert len(runs) == 3
    assert [r["ticker"] for r in runs] == ["DDD", "CCC", "BBB"]   # newest first
    assert runs[0]["error_step"] == "計算五情境並驗證"
    assert "模擬失敗" in runs[0]["error"]
