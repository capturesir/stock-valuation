"""Unadjusting and total return (moved from the backend in alpha.3 step 10): offline, no database."""
from __future__ import annotations

import datetime as dt

from libs.sv_market.prices import _final_cutoff, total_return, unadjust
from libs.sv_market.sources import SourceBar, YahooSource, fx_symbol

D = dt.date


def bar(d, close, adj=None, dividend=0.0, split=0.0):
    return SourceBar(d, close, close, close, close, adj, 1000, dividend, split)


def test_a_split_adjusted_source_is_unadjusted():
    # the source reports a 2-for-1 split on day 3: earlier prices are halved, earlier volume doubled
    src = [bar(D(2024, 1, 1), 50, dividend=0.5), bar(D(2024, 1, 2), 51), bar(D(2024, 1, 3), 52, split=2)]
    bars, actions = unadjust(src)
    assert [b.close for b in bars] == [100, 102, 52] and [b.volume for b in bars] == [500, 500, 1000]
    assert actions == [(D(2024, 1, 1), "dividend", 1.0), (D(2024, 1, 3), "split", 2)]


def test_total_return_reinvests_dividends_and_ends_at_the_last_close():
    closes = [(D(2024, 1, 1), 100.0), (D(2024, 1, 2), 98.0), (D(2024, 1, 3), 99.0)]
    adj = total_return(closes, [(D(2024, 1, 2), "dividend", 2.0)])
    assert adj[-1] == 99.0 and adj[1] == 98.0 and abs(adj[0] - 100 * 0.98) < 1e-12


def test_final_cutoff_and_helpers():
    after = dt.datetime(2024, 3, 5, 22, 0, tzinfo=dt.timezone.utc)      # 17:00 in New York
    before = dt.datetime(2024, 3, 5, 15, 0, tzinfo=dt.timezone.utc)
    assert _final_cutoff("America/New_York", after) == D(2024, 3, 5)
    assert _final_cutoff("America/New_York", before) == D(2024, 3, 4)
    assert fx_symbol("usd", "hkd") == "USDHKD=X" and YahooSource().guard is None
