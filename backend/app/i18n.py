"""API message catalogue. Every error carries a stable ``code`` (frontends translate by code)
and a ``error`` text in the caller's language, chosen from Accept-Language."""
from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

LANGS = ("zh-Hant", "zh-Hans", "en")
DEFAULT = "zh-Hant"

MESSAGES: dict[str, dict[str, str]] = {
    "invalid_ticker": {"zh-Hant": "「{ticker}」不是有效的股票代號。", "zh-Hans": "“{ticker}”不是有效的股票代码。",
                       "en": "“{ticker}” is not a valid ticker."},
    "ticker_required": {"zh-Hant": "請輸入股票代號。", "zh-Hans": "请输入股票代码。", "en": "Enter a ticker."},
    "not_found": {"zh-Hant": "找不到：{what}", "zh-Hans": "找不到：{what}", "en": "Not found: {what}"},
    "job_not_found": {"zh-Hant": "找不到此工作。", "zh-Hans": "找不到此任务。", "en": "Job not found."},
    "job_busy": {"zh-Hant": "{ticker} 已有估值正在進行。", "zh-Hans": "{ticker} 已有估值正在进行。",
                 "en": "A valuation of {ticker} is already running."},
    "over_budget": {"zh-Hant": "預估費用 US${cost:.3f} 超出預算（本次上限 US${run:.2f}，本月剩餘 US${left:.2f}）。",
                    "zh-Hans": "预估费用 US${cost:.3f} 超出预算（本次上限 US${run:.2f}，本月剩余 US${left:.2f}）。",
                    "en": "Estimated cost US${cost:.3f} exceeds the budget (per run US${run:.2f}, left this month US${left:.2f})."},
    "upstream": {"zh-Hant": "上游資料來源暫時無法連線：{detail}", "zh-Hans": "上游数据源暂时无法连接：{detail}",
                 "en": "The upstream data source is unavailable: {detail}"},
    "no_excel": {"zh-Hant": "這次估值沒有 Excel 檔。", "zh-Hans": "这次估值没有 Excel 文件。", "en": "This run has no Excel file."},
    "not_in_watchlist": {"zh-Hant": "{ticker} 不在觀察名單。", "zh-Hans": "{ticker} 不在自选列表。",
                         "en": "{ticker} is not on the watchlist."},
    "bad_request": {"zh-Hant": "{detail}", "zh-Hans": "{detail}", "en": "{detail}"},
    "unauthorized": {"zh-Hant": "需要有效的 API 令牌。", "zh-Hans": "需要有效的 API 令牌。", "en": "A valid API token is required."},
    "ai_not_configured": {"zh-Hant": "尚未設定 AI 位址與模型。", "zh-Hans": "尚未设置 AI 地址与模型。",
                          "en": "The AI endpoint and model are not set."},
}


def pick(accept_language: Optional[str]) -> str:
    """Best supported language from an Accept-Language header (zh-TW/HK/MO → zh-Hant, zh-CN/SG → zh-Hans)."""
    if not accept_language:
        return DEFAULT
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip().lower()
        if not tag:
            continue
        if tag in ("zh-hant", "zh-tw", "zh-hk", "zh-mo") or tag.startswith("zh-hant"):
            return "zh-Hant"
        if tag in ("zh-hans", "zh-cn", "zh-sg", "zh") or tag.startswith("zh-hans"):
            return "zh-Hans"
        if tag.startswith("en"):
            return "en"
    return DEFAULT


def lang_dep(accept_language: Optional[str] = Header(default=None)) -> str:
    return pick(accept_language)


def msg(code: str, lang: str = DEFAULT, **kw) -> str:
    table = MESSAGES.get(code) or MESSAGES["bad_request"]
    text = table.get(lang) or table[DEFAULT]
    try:
        return text.format(**kw)
    except (KeyError, ValueError, IndexError):
        return text


def http_error(status: int, code: str, lang: str = DEFAULT, **kw) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "error": msg(code, lang, **kw)})
