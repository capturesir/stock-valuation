"""Ticker normalisation and the path-safety check (cases/<ticker>/, out/<date>_<ticker>/)."""
from __future__ import annotations

import pytest

from src.pipeline import adjustments, orchestrator
from src.pipeline.tickers import InvalidTicker, normalize


@pytest.mark.parametrize("raw,expect", [
    ("nvda", "NVDA"),
    (" 1830.hk ", "1830.HK"),
    ("5", "0005.HK"),
    ("883.HK", "0883.HK"),
    ("BRK-B", "BRK-B"),
    ("^HSI", "^HSI"),
    ("EURUSD=X", "EURUSD=X"),
    ("M&M.NS", "M&M.NS"),
    ("005930.KS", "005930.KS"),
    ("", ""),
])
def test_normalize_accepts_real_symbols(raw, expect):
    assert normalize(raw) == expect


@pytest.mark.parametrize("raw", [
    "../../tmp/x", "..", "NVDA/../x", "a\\b", "NV DA", "NVDA;rm", "A" * 40, ".HK", "-X",
])
def test_normalize_rejects_paths_and_junk(raw):
    with pytest.raises(InvalidTicker):
        normalize(raw)


def test_both_pipeline_normalisers_share_the_check():
    assert orchestrator._norm("5") == adjustments.norm_ticker("5") == "0005.HK"
    for fn in (orchestrator._norm, adjustments.norm_ticker):
        with pytest.raises(InvalidTicker):
            fn("../x")


def test_valuation_refuses_bad_ticker_before_touching_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "ROOT", tmp_path)
    with pytest.raises(InvalidTicker):
        orchestrator.run_ticker("../../escape", cfg={}, use_llm=False)
    assert not any(tmp_path.iterdir())


def test_cli_reports_bad_ticker_without_traceback(capsys):
    from src.pipeline import cli
    assert cli.main(["run", "--ticker", "../../tmp/x"]) == 2
    assert "不是有效的股票代號" in capsys.readouterr().out


@pytest.mark.parametrize("method,url,body", [
    ("get", "/api/lookup?ticker=../../etc", None),
    ("post", "/api/valuate", {"ticker": "../../tmp/x"}),
])
def test_web_answers_400_for_bad_ticker(client, method, url, body):
    r = getattr(client, method)(url, json=body) if body else getattr(client, method)(url)
    assert r.status_code == 400, r.text
    assert "不是有效的股票代號" in r.json()["detail"]["error"]
