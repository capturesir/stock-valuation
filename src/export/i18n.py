"""Localisation helper for Excel workbooks and Markdown valuation reports (alpha.3 step 10).

Reads ``locales/export.json`` (zh-Hant / zh-Hans / en). When ``lang`` is omitted or ``zh-Hant``,
every sheet name, heading and label matches the original v1 output verbatim so v1 CLI and tests
remain unchanged.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from ..pipeline import gaps as gap_i18n

LANGS = ("zh-Hant", "zh-Hans", "en")
DEFAULT = "zh-Hant"

_FONTS = {
    "zh-Hant": "Microsoft JhengHei",
    "zh-Hans": "Microsoft YaHei",
    "en": "Calibri",
}

_SCENARIO_KEYS = {
    "悲觀": "scenario.bear",
    "基準": "scenario.base",
    "樂觀": "scenario.bull",
    "靜態": "scenario.static",
    "9%靜態": "scenario.static_9",
}

_KIND_KEYS = {
    "市場數據": "kind.market",
    "分析師假設": "kind.analyst",
    "市場數據＋分析師假設": "kind.market_analyst",
    "計算": "kind.calc",
    "輸入": "kind.input",
}


def normalize_lang(lang: Optional[str]) -> str:
    if not lang:
        return DEFAULT
    tag = lang.strip().lower()
    if tag in ("zh-hant", "zh-tw", "zh-hk", "zh-mo") or tag.startswith("zh-hant"):
        return "zh-Hant"
    if tag in ("zh-hans", "zh-cn", "zh-sg", "zh") or tag.startswith("zh-hans"):
        return "zh-Hans"
    if tag.startswith("en"):
        return "en"
    return DEFAULT


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, str]]:
    return json.loads((Path(__file__).parent / "locales" / "export.json").read_text(encoding="utf-8"))


def t(key: str, lang: str = DEFAULT, **params: Any) -> str:
    code = normalize_lang(lang)
    entry = catalog()[key]
    tpl = entry.get(code) or entry[DEFAULT]
    return tpl.format(**params) if params else tpl


def font_name(lang: str = DEFAULT) -> str:
    return _FONTS.get(normalize_lang(lang), _FONTS[DEFAULT])


def scenario_name(name: Optional[str], lang: str = DEFAULT) -> str:
    if not name:
        return ""
    key = _SCENARIO_KEYS.get(name)
    return t(key, lang) if key else name


def kind_label(kind: Optional[str], lang: str = DEFAULT) -> str:
    if not kind:
        return ""
    key = _KIND_KEYS.get(kind)
    return t(key, lang) if key else kind


def render_gaps(d: dict, lang: str = DEFAULT) -> list[str]:
    """Render valuation data gaps in ``lang`` from ``data_gap_codes`` when present, else ``data_gaps``."""
    code = normalize_lang(lang)
    raw = d.get("data_gap_codes") or d.get("data_gaps") or []
    return [gap_i18n.text(item, code) for item in raw]
