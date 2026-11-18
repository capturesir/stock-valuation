"""API v1: health, i18n errors, watchlist, settings (key never returned), plan, jobs, history, usage."""
from __future__ import annotations

import json
import time

import pytest

from backend.app import pricing


def _fake_calc(ticker: str, fair: float = 41.15, price: float = 22.72, usage_events=None) -> dict:
    return {
        "ticker": ticker, "asset": "示例能源控股", "valuation_date": "2026-11-17", "currency": "CNY",
        "fx_if_any": {"pair": "CNY/HKD"}, "price": price, "price_date": "2026-11-17",
        "fair_value_per_share": fair, "assumption_source": "llm" if usage_events else "rules_fallback",
        "discount": {"wacc": {"value": 0.0847}}, "terminal": {"g": 0.025},
        "scenarios": [{"name": "悲觀", "fair_value": 29.1}, {"name": "基準", "fair_value": fair},
                      {"name": "樂觀", "fair_value": 49.8}, {"name": "靜態", "fair_value": 24.6},
                      {"name": "9%靜態", "fair_value": 21.9}],
        "data_gaps": ["本檔為【自動建立的通用規格】"],
        "llm_usage": {"events": usage_events or []},
    }


def _install_fake_engine(monkeypatch, *, usage_events=None, slow=False, fail=None):
    from src.pipeline import orchestrator

    calls = {}

    def fake_run_ticker(ticker, cfg=None, use_llm=True, valuation_date="", progress=None, should_stop=None,
                        skip_extract=False):
        calls.update(ticker=ticker, use_llm=use_llm, skip_extract=skip_extract, cfg=cfg)
        for key in ("quote", "fundamentals", "rf", "filings", "ai_extract", "ai_assume", "spec", "engine", "export", "state"):
            if should_stop and should_stop():
                raise orchestrator.ValuationCancelled("stopped")
            if progress:
                progress(key, f"step {key}", "略過" if key == "ai_extract" else None)
            if slow:
                time.sleep(0.05)
        if fail:
            raise RuntimeError(fail)
        return {"json": _fake_calc(ticker, usage_events=usage_events), "path": None, "xlsx": None,
                "verify_pass": True, "verify_errors": []}

    monkeypatch.setattr(orchestrator, "run_ticker", fake_run_ticker)
    return calls


def _wait(client, job_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/v1/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["database"] == "ok" and body["queue"] == "inline"
    assert body["version"].startswith("2.")


def test_openapi_contract_is_served(client):
    spec = client.get("/api/v1/openapi.json").json()
    for path in ("/api/v1/watchlist", "/api/v1/ai/plan", "/api/v1/valuations", "/api/v1/jobs/{job_id}/events",
                 "/api/v1/settings/ai", "/api/v1/stocks/{ticker}/valuations"):
        assert path in spec["paths"], path


@pytest.mark.parametrize("lang,expect", [("en-US,en;q=0.9", "not a valid ticker"), ("zh-CN", "不是有效的股票代码"),
                                         ("zh-HK", "不是有效的股票代號"), (None, "不是有效的股票代號")])
def test_errors_follow_accept_language(client, lang, expect):
    headers = {"Accept-Language": lang} if lang else {}
    r = client.post("/api/v1/watchlist", json={"ticker": "../../etc"}, headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_ticker"
    assert expect in r.json()["detail"]["error"]


def test_watchlist_add_list_remove(client):
    assert client.post("/api/v1/watchlist", json={"ticker": "883.hk"}).status_code == 201
    items = client.get("/api/v1/watchlist").json()
    assert [i["ticker"] for i in items] == ["0883.HK"] and items[0]["status"] == "never_valued"
    assert client.delete("/api/v1/watchlist/0883.HK").status_code == 204
    r = client.delete("/api/v1/watchlist/0883.HK", headers={"Accept-Language": "en"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_in_watchlist"


def test_api_key_is_write_only_and_encrypted(configured):
    body = configured.get("/api/v1/settings/ai").json()
    assert body["configured"] is True
    assert body["api_key"] == {"set": True, "length": 22, "tail": "abcd"}
    assert "sk-test" not in json.dumps(body)
    from sqlalchemy import text

    from backend.app.db import session
    with session() as db:
        raw = json.dumps(db.execute(text("select value from settings where key='ai'")).scalar_one())
    assert "sk-test" not in raw and "api_key_enc" in raw
    # an empty key keeps the old one
    configured.put("/api/v1/settings/ai", json={"api_key": "", "model": "deepseek-flash"})
    assert configured.get("/api/v1/settings/ai").json()["api_key"]["tail"] == "abcd"


def test_plan_without_key_runs_nothing(client):
    p = client.get("/api/v1/ai/plan", params={"ticker": "0883.HK", "mode": "standard"}).json()
    assert not any(t["will_run"] for t in p["tasks"])
    assert {t["skip_code"] for t in p["tasks"]} == {"no_ai_key"}
    assert p["total"]["cost_usd"] == 0


def test_plan_prices_only_tasks_that_will_run(configured):
    p = configured.get("/api/v1/ai/plan", params={"ticker": "0883.HK", "mode": "standard"}).json()
    run = [t for t in p["tasks"] if t["will_run"]]
    assert [t["key"] for t in run] == ["T3"]
    expect = (10_000 * 0.30 + 3_000 * 1.20) / 1e6
    assert p["total"]["cost_usd"] == pytest.approx(expect)
    assert p["total"]["cost_high_usd"] == pytest.approx(expect * 1.15)
    later = {t["key"]: t["skip_code"] for t in p["tasks"] if not t["will_run"]}
    assert later["T1"] == "next_phase" and later["T7"] == "next_phase"
    # built-in spec: assumptions are locked, so T3 is skipped
    nv = configured.get("/api/v1/ai/plan", params={"ticker": "NVDA", "mode": "standard"}).json()
    assert next(t for t in nv["tasks"] if t["key"] == "T3")["skip_code"] == "name_spec_locked"


def test_price_lookup_prefers_longest_prefix():
    prices = [{"model": "deepseek", "input": 9, "output": 9}, {"model": "deepseek-flash", "input": 0.3, "output": 1.2}]
    assert pricing.find_price("deepseek-flash-2026", prices)["input"] == 0.3
    assert pricing.find_price("unknown-model", prices) is None
    assert pricing.cost(1_000_000, 0, {"input": 0.3, "cached_input": 0.006, "output": 1.2}, cached_in=500_000) == \
        pytest.approx(0.5 * 0.3 + 0.5 * 0.006)


def test_valuation_job_records_progress_cost_and_history(configured, monkeypatch):
    events = [{"tag": "propose_assumptions", "reported": True, "prompt_tokens": 9_500, "completion_tokens": 2_800}]
    calls = _install_fake_engine(monkeypatch, usage_events=events)
    r = configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "standard"})
    assert r.status_code == 202, r.text
    job = _wait(configured, r.json()["id"])
    assert job["status"] == "done" and job["progress"] == 100
    assert calls["use_llm"] is True and calls["skip_extract"] is True
    assert calls["cfg"]["llm"]["api_key"] == "sk-test-1234567890abcd"      # decrypted server-side only
    t3 = next(s for s in job["steps"] if s["key"] == "T3")
    assert t3["status"] == "done" and t3["tokens_in"] == 9_500
    assert job["cost_usd"] == pytest.approx((9_500 * 0.30 + 2_800 * 1.20) / 1e6)
    assert all(s["status"] in ("done", "skipped") for s in job["steps"])

    hist = configured.get("/api/v1/stocks/0883.HK/valuations").json()
    assert hist["currency"] == "HKD" and hist["latest"]["composite_value"] == pytest.approx(41.15)
    assert {m["scenario"] for m in hist["latest"]["methods"]} == {"bear", "base", "bull", "static", "floor9"}
    assert hist["latest"]["ai_cost_usd"] == pytest.approx(job["cost_usd"])

    usage = configured.get("/api/v1/ai/usage").json()
    assert usage["total_usd"] == pytest.approx(job["cost_usd"])
    assert usage["by_task"][0]["task"] == "T3" and usage["by_ticker"][0]["ticker"] == "0883.HK"


def test_watchlist_status_uses_latest_run(configured, monkeypatch):
    _install_fake_engine(monkeypatch)
    configured.post("/api/v1/watchlist", json={"ticker": "0883.HK"})
    _wait(configured, configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"}).json()["id"])
    entry = configured.get("/api/v1/watchlist").json()[0]
    assert entry["status"] == "below_bear"          # price 22.72 < bear 29.10
    assert entry["company"] == "示例能源控股"


def test_mode_none_never_calls_ai(configured, monkeypatch):
    calls = _install_fake_engine(monkeypatch)
    job = _wait(configured, configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"}).json()["id"])
    assert calls["use_llm"] is False and job["cost_usd"] == 0


def test_over_budget_falls_back_or_refuses(configured, monkeypatch):
    calls = _install_fake_engine(monkeypatch)
    configured.put("/api/v1/settings/ai", json={"budget_per_run_usd": 0.0001})
    job = _wait(configured, configured.post("/api/v1/valuations", json={"ticker": "0883.HK"}).json()["id"])
    assert calls["use_llm"] is False
    assert next(s for s in job["steps"] if s["key"] == "T3")["note_code"] == "over_budget"
    configured.put("/api/v1/settings/ai", json={"over_budget": "ask"})
    r = configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "force": True}, headers={"Accept-Language": "en"})
    assert r.status_code == 402 and "exceeds the budget" in r.json()["detail"]["error"]


def test_cancel_keeps_a_cancelled_run(configured, monkeypatch):
    _install_fake_engine(monkeypatch, slow=True)
    job_id = configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"}).json()["id"]
    time.sleep(0.12)
    configured.post(f"/api/v1/jobs/{job_id}/cancel")
    job = _wait(configured, job_id)
    assert job["status"] == "cancelled"
    runs = configured.get("/api/v1/stocks/0883.HK/valuations").json()["runs"]
    assert runs[-1]["status"] == "cancelled" and runs[-1]["error"]


def test_failed_run_reports_error(configured, monkeypatch):
    _install_fake_engine(monkeypatch, fail="Yahoo 無回應")
    job = _wait(configured, configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"}).json()["id"])
    assert job["status"] == "failed" and "Yahoo 無回應" in job["error"]


def test_second_request_for_a_running_ticker_joins_that_job(configured, member, monkeypatch):
    _install_fake_engine(monkeypatch, slow=True)
    first = configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"})
    second = member.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"})
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["joined"] is True and second.json()["id"] == first.json()["id"]
    # the member joined but did not start it: only the starter (or an admin) may stop it
    assert member.post(f"/api/v1/jobs/{first.json()['id']}/cancel").json()["detail"]["code"] == "not_your_job"
    _wait(configured, first.json()["id"])
def test_sse_stream_ends_with_terminal_state(configured, monkeypatch):
    _install_fake_engine(monkeypatch, slow=True)
    job_id = configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none"}).json()["id"]
    states = []
    with configured.stream("GET", f"/api/v1/jobs/{job_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                states.append(json.loads(line[6:])["status"])
    assert states[-1] == "done" and len(states) >= 2


def test_legacy_import_reads_v1_outputs(client, tmp_path):
    import shutil

    from backend.app import ROOT
    from backend.app.db import session
    from backend.app.services.legacy import import_legacy

    # Copy only the two versioned v1 runs: the real out/ also holds whatever the user has
    # valued locally, which must not change the outcome of this test.
    out = tmp_path / "out"
    for name in ("2026-11-11_NVDA", "2026-11-11_1830.HK"):
        shutil.copytree(ROOT / "out" / name, out / name)
    with session() as db:
        n = import_legacy(db, out_dir=out)
        again = import_legacy(db, out_dir=out, force=True)
    assert n == 2 and again == 0                        # NVDA and 1830.HK from out/, idempotent
    nv = client.get("/api/v1/stocks/NVDA/valuations").json()
    assert nv["latest"]["source"] == "legacy" and nv["latest"]["verify_pass"] is True
    assert nv["latest"]["has_excel"] is True


def test_run_is_recorded_under_the_requested_date(configured, monkeypatch):
    # built-in specs (NVDA, 1830.HK) report their fixed assumption-review date in the calc JSON;
    # the run must still be recorded for the day it was made for
    _install_fake_engine(monkeypatch)                    # fake calc says valuation_date 2026-11-17
    job_id = configured.post("/api/v1/valuations",
                             json={"ticker": "0883.HK", "mode": "none", "valuation_date": "2026-11-18"}).json()["id"]
    _wait(configured, job_id)
    latest = configured.get("/api/v1/stocks/0883.HK/valuations").json()["latest"]
    assert latest["valuation_date"] == "2026-11-18"


def test_legacy_run_time_keeps_its_offset(client, tmp_path):
    # v1 logged local (Macau) wall time without an offset; it must come back as the same
    # wall time, not shifted by 8 hours
    import json as _json

    from backend.app.db import session
    from backend.app.services.legacy import import_legacy

    hist = tmp_path / "out" / "history"
    hist.mkdir(parents=True)
    (hist / "AAA.json").write_text(_json.dumps([{
        "ticker": "AAA", "run_at": "2026-11-17T21:40:42", "valuation_date": "2026-11-17",
        "price": 10.0, "price_date": "2026-11-17", "fair_value": 12.0, "scenarios": {"基準": 12.0}}]),
        encoding="utf-8")
    with session() as db:
        assert import_legacy(db, out_dir=tmp_path / "out") == 1
    run = client.get("/api/v1/stocks/AAA/valuations").json()["latest"]
    assert run["run_at"] == "2026-11-17T21:40:42+08:00"


def test_legacy_import_counts_one_run_once(client, tmp_path):
    # a built-in-spec run is written to out/<run date>_<ticker>/ and to the v1 run log under the
    # spec's fixed review date; both describe the same run and must be imported once
    import json as _json
    import shutil

    from backend.app import ROOT
    from backend.app.db import session
    from backend.app.services.legacy import import_legacy

    out = tmp_path / "out"
    src = ROOT / "out" / "2026-11-11_NVDA"
    shutil.copytree(src, out / "2026-11-18_NVDA")
    for f in list((out / "2026-11-18_NVDA").iterdir()):
        f.rename(f.with_name(f.name.replace("2026-11-11", "2026-11-18")))
    calc = _json.loads((src / "2026-11-11_NVDA.json").read_text(encoding="utf-8"))
    (out / "history").mkdir()
    (out / "history" / "NVDA.json").write_text(_json.dumps([{
        "ticker": "NVDA", "run_at": "2026-11-18T00:37:52", "valuation_date": "2026-11-11",
        "price": calc["price"], "price_date": calc["price_date"], "fair_value": calc["fair_value_per_share"]}]),
        encoding="utf-8")
    with session() as db:
        assert import_legacy(db, out_dir=out) == 1


def test_each_run_keeps_its_own_files(configured, monkeypatch, tmp_path):
    # two runs of one ticker on one day: the v1 engine reuses out/<date>_<ticker>/, so the second run
    # used to overwrite the first run's Excel while both records pointed at it
    from src.pipeline import orchestrator

    shared = tmp_path / "out" / "2026-11-18_0883.HK"
    shared.mkdir(parents=True)
    calls = {"n": 0}

    def fake_run_ticker(ticker, cfg=None, use_llm=True, valuation_date="", progress=None, should_stop=None,
                        skip_extract=False):
        calls["n"] += 1
        (shared / "2026-11-18_0883.HK_FCFF.xlsx").write_text(f"excel of run {calls['n']}")
        (shared / "2026-11-18_0883.HK.json").write_text(f"json of run {calls['n']}")
        return {"json": _fake_calc(ticker, fair=40.0 + calls["n"]), "path": str(shared / "2026-11-18_0883.HK.json"),
                "xlsx": str(shared / "2026-11-18_0883.HK_FCFF.xlsx"), "verify_pass": True, "verify_errors": []}

    monkeypatch.setattr(orchestrator, "run_ticker", fake_run_ticker)
    for _ in range(2):   # force: the second run is a deliberate re-run inside the cool-down window
        _wait(configured, configured.post("/api/v1/valuations", json={"ticker": "0883.HK", "mode": "none",
                                                                      "force": True}).json()["id"])
    runs = configured.get("/api/v1/stocks/0883.HK/valuations").json()["runs"]
    assert len(runs) == 2
    bodies = [configured.get(f"/api/v1/runs/{r['id']}/excel").text for r in runs]
    assert sorted(bodies) == ["excel of run 1", "excel of run 2"]
