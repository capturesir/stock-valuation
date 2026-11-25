"""Data gaps as code + parameters: every language has every gap, with the same placeholders."""
from __future__ import annotations

import json
import string

from src.pipeline.gaps import LANGS, Gap, catalog, encode, text


def _fields(tpl: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(tpl) if f}


def test_every_gap_has_three_languages_with_the_same_placeholders():
    for code, entry in catalog().items():
        assert set(entry) == set(LANGS), code
        names = {lang: _fields(t) for lang, t in entry.items()}
        # each language may name the country by code or by name, but uses only known parameters
        assert names["zh-Hans"] == names["zh-Hant"], code
        assert names["en"] <= names["zh-Hant"] | {"country", "listing"}, code


def test_a_gap_is_its_traditional_chinese_text_and_renders_in_other_languages():
    g = Gap("consensus_clamped", label="0y", x=1.8, lo=-0.5, hi=1.5)
    assert g == "營收共識0y 180.0% 超出合理範圍（-50%～150%），已截斷為邊界值。" and "超出合理範圍" in g
    stored = json.loads(json.dumps(encode([g, "a hand-written note"])))
    assert stored[1] == {"code": None, "params": {}, "text": "a hand-written note"}
    assert text(stored[0], "en").startswith("Revenue consensus 0y of 180.0% is outside")
    assert text(stored[1], "en") == "a hand-written note" and text("legacy text", "en") == "legacy text"


def test_nested_gaps_and_lists_follow_the_language():
    g = Gap("sec_standard", ruleset="general", version="1", plan=Gap("plan_central", v=3), years=["FY2024", "FY2023"])
    assert "中央方案 v3" in g and "FY2024、FY2023" in g
    e = encode([g])[0]
    assert "central plan v3" in text(e, "en") and "FY2024, FY2023" in text(e, "en")
    assert text({"code": "no_such_code", "params": {}, "text": "saved"}, "en") == "saved"
