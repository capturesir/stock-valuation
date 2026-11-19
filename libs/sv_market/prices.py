"""Unadjusted storage, total return and the check against the source's adjusted close
(moved from backend/app/services/market.py in alpha.3 step 10; behaviour unchanged). Pure functions:
nothing here reads or writes the database.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from .sources import SourceBar

FIELDS = ("open", "high", "low", "close")
REL_TOL = 1e-5                  # differences below 0.001 % are rounding, not revisions
INCREMENTAL_DAYS = 14           # overlap re-read on each update, to catch late corrections
CLOSE_HOUR = 17                 # local exchange time after which today's bar counts as final
ADJ_CHECK_TOL = 0.001           # total-return series must match the source within 0.1 %
USD_HKD_BAND = (7.70, 7.90)     # Linked Exchange Rate band (7.75–7.85) with a little room for the source's FX
DIV_MATCH_TOL = 0.02            # a source dividend step within 2 % of the standard one counts as the same


@dataclass
class Bar:
    date: dt.date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: float
    volume: Optional[int]


def unadjust(bars: list[SourceBar]) -> tuple[list[Bar], list[tuple[dt.date, str, float]]]:
    """Undo the source's split adjustment. Factor for a day = product of split ratios strictly after it."""
    bars = sorted(bars, key=lambda b: b.date)
    factor = 1.0
    out: list[Bar] = []
    actions: list[tuple[dt.date, str, float]] = []
    for b in reversed(bars):
        f = factor
        out.append(Bar(b.date, *(None if v is None else v * f for v in (b.open, b.high, b.low)), b.close * f,
                       None if b.volume is None else int(round(b.volume / f))))
        if b.dividend:
            actions.append((b.date, "dividend", b.dividend * f))
        if b.split and b.split > 0 and b.split != 1:
            actions.append((b.date, "split", b.split))
            factor *= b.split               # every earlier day was priced before this split
    out.reverse()
    return out, sorted(actions)


def total_return(bars: list[tuple[dt.date, float]], actions: Iterable[tuple[dt.date, str, float]]) -> list[float]:
    """Split- and dividend-adjusted closes, scaled so the latest equals the latest close
    (the convention data vendors use for "Adj Close"). ``bars`` are unadjusted (date, close)."""
    by_date: dict[dt.date, list[tuple[str, float]]] = {}
    for d, kind, v in actions:
        by_date.setdefault(d, []).append((kind, v))
    n = len(bars)
    adj = [0.0] * n
    factor = 1.0
    for i in range(n - 1, -1, -1):
        d, close = bars[i]
        adj[i] = close * factor
        for kind, v in by_date.get(d, []):
            if kind == "split" and v > 0:
                factor /= v
            elif kind == "dividend" and i > 0 and bars[i - 1][1] > 0:
                factor *= max(0.0, 1 - v / bars[i - 1][1])
    return adj


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _final_cutoff(tz: Optional[str], now: Optional[dt.datetime] = None) -> dt.date:
    """Latest date whose bar is final: today after the local close, otherwise yesterday."""
    now = now or _now()
    try:
        local = now.astimezone(ZoneInfo(tz or "America/New_York"))
    except Exception:
        local = now.astimezone(ZoneInfo("America/New_York"))
    return local.date() if local.hour >= CLOSE_HOUR else local.date() - dt.timedelta(days=1)


def _differs(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return abs(a - b) > max(1e-9, REL_TOL * max(abs(a), abs(b)))


def _iso(d: Optional[dt.date]) -> Optional[str]:
    return d.isoformat() if d else None


def _max_deviation(pairs, bars: list[Bar], actions) -> tuple[float, Optional[dt.date]]:
    """Largest relative gap between our series and the source's adjusted close, both scaled to the latest day."""
    ours_by_date = dict(zip((b.date for b in bars), total_return([(b.date, b.close) for b in bars], actions)))
    last_src, last_ours = pairs[-1][0].adj_close, ours_by_date[pairs[-1][1].date]
    worst, worst_date = 0.0, None
    for sb, b in pairs:
        s = sb.adj_close / last_src
        if s > 0:
            dev = abs((ours_by_date[b.date] / last_ours) / s - 1)
            if dev > worst:
                worst, worst_date = dev, b.date
    return worst, worst_date


def _source_dividend_steps(source_bars: list[SourceBar]) -> list[dict]:
    """For each ex-date, the dividend the source's adjusted close actually deducted:
    step = (adj/close the day before) / (adj/close on the day) = 1 − D_applied / previous close."""
    sb = sorted(source_bars, key=lambda x: x.date)
    out = []
    for i in range(1, len(sb)):
        b, prev = sb[i], sb[i - 1]
        if not b.dividend or not (b.adj_close and prev.adj_close and b.close and prev.close):
            continue
        step = (prev.adj_close / prev.close) / (b.adj_close / b.close)
        applied = (1 - step) * prev.close
        out.append({"date": b.date, "dividend": b.dividend, "applied": applied,
                    "ratio": b.dividend / applied if applied > 0 else None})
    return out


def _check_total_return(source_bars: list[SourceBar], bars: list[Bar], actions, currency: Optional[str] = None) -> dict:
    """Our total-return series vs the source's adjusted close.

    When they differ, look for the one known source error: a dividend declared in USD but traded in
    HKD (HSBC 0005.HK, MINISO 9896.HK) whose USD amount Yahoo deducts from the HKD price, so its step
    is ~7.8 times too small. If every mismatching ex-date has that ratio, and repeating the source's
    mistake reproduces its series within tolerance, the gap is explained: our figure is the correct one."""
    src_sorted = sorted(source_bars, key=lambda x: x.date)
    pairs = [(sb, b) for sb, b in zip(src_sorted, bars) if sb.adj_close]
    if len(pairs) < 2:
        return {"status": "skipped", "reason_code": "no_source_adjusted", "reason": "來源沒有調整後價格"}
    worst, worst_date = _max_deviation(pairs, bars, actions)
    out = {"status": "ok" if worst <= ADJ_CHECK_TOL else "warn", "max_deviation": round(worst, 6),
           "at": _iso(worst_date), "tolerance": ADJ_CHECK_TOL, "checked_at": _now().isoformat(timespec="seconds")}
    if out["status"] == "ok":
        return out
    steps = _source_dividend_steps(source_bars)
    wrong = [e for e in steps if e["ratio"] is None or abs(e["ratio"] - 1) > DIV_MATCH_TOL]
    lo, hi = USD_HKD_BAND
    usd = [e for e in wrong if e["ratio"] and lo <= e["ratio"] <= hi]
    if (currency or "").upper() == "HKD" and wrong and len(usd) == len(wrong):
        # repeat the source's mistake on those dates; what is left must be within tolerance
        fix = {e["date"]: e["ratio"] for e in usd}
        mimic = [(d, k, v / fix[d]) if k == "dividend" and d in fix else (d, k, v) for d, k, v in actions]
        residual, _ = _max_deviation(pairs, bars, mimic)
        if residual <= ADJ_CHECK_TOL:
            out.update(status="explained", residual_deviation=round(residual, 6), note_code="source_usd_dividend",
                       events=[{"date": _iso(e["date"]), "dividend": round(e["dividend"], 6),
                                "source_applied": round(e["applied"], 6)} for e in usd],
                       note=("來源的調整後價格把以美元宣派的派息當作港元扣減（約少扣 7.8 倍）；"
                             "系統的總回報按港元派息計算，是正確的數字。"))
            return out
    out["note_code"] = "adjusted_mismatch"
    out["note"] = ("系統計算的總回報與來源的調整後價格不一致，需要人工覆核。常見原因：來源以錯誤貨幣計算派息"
                   "（例如以美元宣派、港元交易的股票）、或漏計供股等公司行動。系統的總回報由未調整價格與派息自行計算。")
    return out
