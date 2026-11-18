"""Check the metrics module against LibreOffice-recalculated spreadsheet formulas on real accumulated data.

    python scripts/verify_metrics.py NVDA SPY --years 3 --rf 0.04

Writes var/metrics-check-<asset>-<benchmark>.xlsx (spreadsheet with formulas and values) and prints both
answers side by side. Needs the dev database (scripts/dev.sh) and LibreOffice.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("asset")
    ap.add_argument("benchmark")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--rf", type=float, default=0.0)
    a = ap.parse_args()
    from backend.app.db import session
    from backend.app.services import market
    from libs.sv_market.series import align
    from libs.sv_market.spreadsheet_check import build, compare, recalculate

    start = dt.date.today() - dt.timedelta(days=round(365.25 * a.years))
    with session() as db:
        s = {t: [(p.date, p.adj_close) for p in market.series(db, t, start)] for t in (a.asset, a.benchmark)}
    dates, vals = align(s)
    if len(dates) < 30:
        print("not enough common dates; is the market data synced?")
        return 1
    out_dir = ROOT / "var"
    out_dir.mkdir(exist_ok=True)
    path = build(out_dir / f"metrics-check-{a.asset}-{a.benchmark}.xlsx", dates, vals[a.asset], vals[a.benchmark], a.rf)
    calc = recalculate(path)
    path.write_bytes(calc.read_bytes())
    worst = 0.0
    print(f"{a.asset} vs {a.benchmark}, {dates[0]} – {dates[-1]}, {len(dates)} days, rf {a.rf:.2%}")
    print(f"{'metric':20}{'spreadsheet':>20}{'module':>20}{'rel. diff':>12}")
    for name, sheet, module in compare(path, dates, vals[a.asset], vals[a.benchmark], a.rf):
        rel = abs(sheet - module) / max(abs(sheet), 1e-12)
        worst = max(worst, rel)
        print(f"{name:20}{sheet:>20.10f}{module:>20.10f}{rel:>12.1e}")
    print(f"largest relative difference: {worst:.1e}  ({'PASS' if worst < 1e-9 else 'FAIL'})   workbook: {path}")
    return 0 if worst < 1e-9 else 1


if __name__ == "__main__":
    sys.exit(main())
