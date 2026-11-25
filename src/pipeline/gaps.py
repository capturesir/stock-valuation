"""Data gaps as code + parameters (alpha.3 step 10, engine output i18n).

A gap is still a ``str`` (its Traditional Chinese text), so the v1 Excel, markdown report and every
``"…" in gaps`` check keep working; it also carries ``code`` and ``params`` so the web app can show it in
the reader's language from ``locales/gaps.json``. Parameters may be numbers (formatted by the template),
text, lists (joined per language) or other gaps (rendered in the same language).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

LANGS = ("zh-Hant", "zh-Hans", "en")
DEFAULT = "zh-Hant"
SEP = {"zh-Hant": "、", "zh-Hans": "、", "en": ", "}


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((Path(__file__).parent / "locales" / "gaps.json").read_text(encoding="utf-8"))


def _plain(v: Any) -> Any:
    """A JSON-safe copy of a parameter (nested gaps become {"code", "params"})."""
    if isinstance(v, Gap):
        return {"code": v.code, "params": v.params}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def render(code: str, params: dict, lang: str = DEFAULT) -> str:
    entry = catalog()[code]
    tpl = entry.get(lang) or entry[DEFAULT]
    vals = {}
    for k, v in (params or {}).items():
        if isinstance(v, dict) and "code" in v:
            v = render(v["code"], v.get("params") or {}, lang)
        elif isinstance(v, list):
            v = SEP.get(lang, ", ").join(render(x["code"], x.get("params") or {}, lang) if isinstance(x, dict) else str(x)
                                         for x in v)
        vals[k] = v
    return tpl.format(**vals)


class Gap(str):
    code: str
    params: dict

    def __new__(cls, code: str, **params):
        plain = {k: _plain(v) for k, v in params.items()}
        obj = super().__new__(cls, render(code, plain, DEFAULT))
        obj.code, obj.params = code, plain
        return obj


def encode(items: Iterable) -> list[dict]:
    """For storage: {"code", "params", "text"}; a plain string (a hand-written note) has code None."""
    return [{"code": g.code, "params": g.params, "text": str(g)} if isinstance(g, Gap)
            else {"code": None, "params": {}, "text": str(g)} for g in items or []]


def text(item: Any, lang: str = DEFAULT) -> str:
    """One stored gap (encoded dict or legacy string) in ``lang``; unknown codes fall back to the saved text."""
    if isinstance(item, dict):
        code = item.get("code")
        if code and code in catalog():
            try:
                return render(code, item.get("params") or {}, lang)
            except (KeyError, ValueError, IndexError):
                pass
        return str(item.get("text") or "")
    return str(item)
