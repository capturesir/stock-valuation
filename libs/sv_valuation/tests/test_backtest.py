"""Unit tests for point-in-time valuation backtest scorecard."""
from __future__ import annotations

from libs.sv_valuation.backtest import BacktestPoint, compute_backtest_scorecard, spearman_ic


def test_backtest_scorecard_computes_rank_ic_spread_and_hit_rate():
    pts = [
        # Undervalued calls that outperformed
        BacktestPoint("AAA", "2023-03-01", fair_value=140.0, price=100.0, fwd_price=135.0, fwd_date="2024-03-01"),
        BacktestPoint("BBB", "2023-03-01", fair_value=65.0, price=50.0, fwd_price=64.0, fwd_date="2024-03-01"),
        # Fairly valued
        BacktestPoint("CCC", "2023-03-01", fair_value=102.0, price=100.0, fwd_price=108.0, fwd_date="2024-03-01"),
        # Overvalued calls that lagged
        BacktestPoint("DDD", "2023-03-01", fair_value=70.0, price=100.0, fwd_price=82.0, fwd_date="2024-03-01"),
        BacktestPoint("EEE", "2023-03-01", fair_value=80.0, price=100.0, fwd_price=90.0, fwd_date="2024-03-01"),
    ]
    sc = compute_backtest_scorecard(pts)
    assert sc["sample_count"] == 5
    assert sc["tickers_tested"] == 5
    assert sc["rank_ic"] is not None and sc["rank_ic"] > 0.8
    assert sc["hit_rate"] == 1.0
    assert sc["undervalued_avg_return"] > sc["overvalued_avg_return"]
    assert sc["spread_under_minus_over"] > 0.4
    assert sc["buckets"] == {"undervalued": 2, "fair": 1, "overvalued": 2}
    assert spearman_ic([1, 2, 3], [1, 2, 3]) == 1.0
