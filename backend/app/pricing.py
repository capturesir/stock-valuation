"""AI task registry, default price table and cost estimates (pure functions, no I/O).

Token budgets come from the v2 plan (§8.3), sized on the measured NVDA statement
payload (~34k characters). Estimates are multiplied by a per-model calibration
factor learned from the provider-reported usage of earlier runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

RETRY_BUFFER = 0.15       # upper end of the range covers one retry in ~7 requests
TOKENS_PER_MILLION = 1_000_000.0

MODES = ("none", "lite", "standard", "deep")
MODE_LABELS = {"none": "不用 AI", "lite": "精簡", "standard": "標準", "deep": "深度"}


@dataclass(frozen=True)
class TaskDef:
    key: str
    label: str
    detail: str
    est_in: int
    est_out: int
    modes: tuple[str, ...]
    available: bool          # implemented in this release
    llm_tag: Optional[str]   # LLMClient usage tag, to match provider-reported usage
    needs_generic_spec: bool = False


TASKS: tuple[TaskDef, ...] = (
    TaskDef("T1", "財報異常偵測與調整建議", "4–5 期三大報表，找出一次性損益、重編、單位錯誤", 12_000, 2_000,
            ("standard", "deep"), False, None),
    TaskDef("T2", "新資訊摘要與影響評估", "最新公告與新聞，評估對增長、利潤率、風險的影響", 20_000, 3_000,
            ("standard", "deep"), False, None),
    TaskDef("T3", "增長與利潤率假設", "10 個預測年的營收增長與 EBIT 利潤率，附理由；超出資料範圍會被拒絕", 10_000, 3_000,
            ("lite", "standard", "deep"), True, "propose_assumptions", needs_generic_spec=True),
    TaskDef("T4", "情境敘事與機率", "悲觀／基準／樂觀的觸發條件與建議機率", 6_000, 2_000, ("deep",), False, None),
    TaskDef("T5", "可比公司篩選", "3–8 家可比公司與入選理由，倍數由程式抓取", 3_000, 1_000, ("deep",), False, None),
    TaskDef("T6", "反方審查", "找出假設之間的矛盾與遺漏的風險", 12_000, 2_000, ("deep",), False, None),
    TaskDef("T7", "估值報告與變動解釋", "投資論點、風險，以及理論價為何改變", 8_000, 2_000,
            ("lite", "standard", "deep"), False, None),
    TaskDef("T8", "分部與驅動因子拆解", "分部營收與增長驅動因子，供 SOTP 使用", 25_000, 3_000, ("deep",), False, None),
)
TASK_BY_KEY = {t.key: t for t in TASKS}
NEXT_PHASE = "下一階段推出（v2.0.0-beta）"
# Stable codes for frontends to translate; the Chinese text is the fallback.
SKIP_REASONS = {
    "mode_none": "已選擇不用 AI",
    "no_ai_key": "未設定 AI 金鑰",
    "next_phase": NEXT_PHASE,
    "name_spec_locked": "內建人工覆核規格已鎖定假設，不呼叫 AI",
    "not_selected": "未勾選",
    "over_budget": "預估超出預算，已改用規則後備",
}

# US$ per million tokens. Third-party trackers, checked 2026-11-17; users edit these
# in the settings page and actual cost always uses provider-reported usage.
DEFAULT_PRICES: tuple[dict, ...] = (
    {"model": "deepseek-flash", "input": 0.30, "cached_input": 0.006, "output": 1.20,
     "note": "DeepSeek V4.1 Flash 尖峰時段；離峰 0.15／0.003／0.60", "checked_on": "2026-11-17",
     "source_url": "https://www.benchlm.ai/deepseek/api-pricing"},
    {"model": "deepseek-v4-pro", "input": 1.32, "cached_input": 0.044, "output": 3.96,
     "note": "尖峰時段", "checked_on": "2026-11-17", "source_url": "https://www.benchlm.ai/deepseek/api-pricing"},
    {"model": "gemini-3.8-flash", "input": 0.75, "cached_input": 0.075, "output": 3.75,
     "note": "優惠價至 2026-12-31", "checked_on": "2026-11-17", "source_url": "https://benchlm.ai/google/api-pricing"},
    {"model": "gpt-6.1-sol", "input": 2.00, "cached_input": 0.10, "output": 10.00,
     "note": "標準層、272k 以內", "checked_on": "2026-11-17", "source_url": "https://www.requesty.ai/blog/openai-api-pricing"},
)

PROVIDER_PRESETS: tuple[dict, ...] = (
    {"id": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash"},
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-6.1-sol"},
    {"id": "gemini", "label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "model": "gemini-3.8-flash"},
    {"id": "custom", "label": "其他相容服務", "base_url": "", "model": ""},
    {"id": "local", "label": "本地模型（Ollama／LM Studio）", "base_url": "http://host.docker.internal:11434/v1",
     "model": "qwen3:8b"},
)


def is_local(base_url: str) -> bool:
    u = (base_url or "").lower()
    return any(s in u for s in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "host.docker.internal"))


def find_price(model: str, prices: Iterable[dict]) -> Optional[dict]:
    """Exact model id first, then the longest table id the model name starts with."""
    m = (model or "").strip().lower()
    table = list(prices)
    for p in table:
        if p["model"].lower() == m:
            return p
    hits = [p for p in table if m.startswith(p["model"].lower())]
    return max(hits, key=lambda p: len(p["model"])) if hits else None


def cost(tokens_in: int, tokens_out: int, price: Optional[dict], cached_in: int = 0) -> Optional[float]:
    if price is None:
        return None
    fresh = max(int(tokens_in) - int(cached_in), 0)
    cached_rate = price.get("cached_input")
    cached_rate = price["input"] if cached_rate is None else cached_rate
    return (fresh * price["input"] + int(cached_in) * cached_rate + int(tokens_out) * price["output"]) / TOKENS_PER_MILLION


def plan(*, mode: str, model: str, prices: Iterable[dict], llm_configured: bool, local: bool,
         has_name_spec: bool, selected: Optional[Iterable[str]] = None, calibration: float = 1.0) -> dict:
    """Task list for one run: what will run, why the rest won't, and what it should cost."""
    if mode not in MODES:
        raise ValueError(f"未知模式：{mode}")
    price = {"model": model, "input": 0.0, "cached_input": 0.0, "output": 0.0} if local else find_price(model, prices)
    chosen = set(selected) if selected is not None else None
    rows, est_in, est_out, est_cost, unpriced = [], 0, 0, 0.0, False
    for t in TASKS:
        in_mode = mode != "none" and mode in t.modes
        picked = in_mode if chosen is None else (t.key in chosen)
        code = None
        if mode == "none":
            code = "mode_none"
        elif not llm_configured:
            code = "no_ai_key"
        elif not t.available:
            code = "next_phase"
        elif t.needs_generic_spec and has_name_spec:
            code = "name_spec_locked"
        elif not picked:
            code = "not_selected"
        reason = SKIP_REASONS.get(code)
        will_run = code is None
        e_in, e_out = round(t.est_in * calibration), round(t.est_out * calibration)
        c = cost(e_in, e_out, price)
        if will_run:
            est_in += e_in
            est_out += e_out
            if c is None:
                unpriced = True
            else:
                est_cost += c
        rows.append({
            "key": t.key, "label": t.label, "detail": t.detail, "in_mode": in_mode, "selected": picked,
            "available": t.available, "will_run": will_run, "skip_reason": reason, "skip_code": code,
            "est_tokens_in": e_in, "est_tokens_out": e_out, "est_cost_usd": c,
        })
    return {
        "mode": mode, "mode_label": MODE_LABELS[mode], "model": model or None,
        "price": price,
        # nothing to price when no task will run (e.g. no key yet): don't warn about the price table
        "priced": est_in == 0 or (not unpriced and price is not None),
        "calibration": round(calibration, 3),
        "tasks": rows,
        "total": {
            "tokens_in": est_in, "tokens_out": est_out,
            "cost_usd": round(est_cost, 6),
            "cost_low_usd": round(est_cost, 6),
            "cost_high_usd": round(est_cost * (1 + RETRY_BUFFER), 6),
        },
    }
