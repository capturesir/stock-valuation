"""One place to normalise and validate a ticker.

The ticker becomes part of file paths (``cases/<ticker>/``, ``out/<date>_<ticker>/``,
``out/history/<ticker>.json``), so it must never contain a path separator or
``..``. Before this check, a ticker such as ``../../tmp/x`` made the pipeline
create folders and write ``yahoo_chart.json`` outside the project.
"""
from __future__ import annotations

import re

# Yahoo symbols: letters, digits and . - = ^ & (BRK-B, ^HSI, EURUSD=X, M&M.NS).
_TICKER_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^&]{0,23}$")


class InvalidTicker(ValueError):
    """The text cannot be a ticker (and must not be used in a file path)."""


def normalize(ticker: str) -> str:
    """Upper-case, pad Hong Kong codes to four digits (5 -> 0005.HK), validate.

    An empty string stays empty so callers that pass ``ticker or ""`` keep
    working; anything else that is not a plausible symbol raises InvalidTicker.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return t
    if t.endswith(".HK") and t[:-3].isdigit():
        t = t[:-3].zfill(4) + ".HK"
    elif t.isdigit() and len(t) <= 4:
        t = t.zfill(4) + ".HK"
    if ".." in t or not _TICKER_RE.fullmatch(t):
        raise InvalidTicker(f"「{ticker}」不是有效的股票代號（只可包含英文字母、數字及 . - = ^ &）。")
    return t
