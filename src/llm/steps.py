"""API-shaped wrappers for the three AI steps. Always go through LLMClient."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .client import LLMClient, LLMResult

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _load(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def extract_financials(client: LLMClient, source_text: str) -> LLMResult:
    return client.chat_json(_load("extract_financials.md"), source_text, tag="extract_financials")


def propose_assumptions(client: LLMClient, source_text: str) -> LLMResult:
    return client.chat_json(_load("propose_assumptions.md"), source_text, tag="propose_assumptions")


def ib_compare(client: LLMClient, source_text: str) -> LLMResult:
    return client.chat_json(_load("ib_compare.md"), source_text, tag="ib_compare")
