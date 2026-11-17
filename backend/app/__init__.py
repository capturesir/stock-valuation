"""Valuation workbench API v2."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


__version__ = _read_version()
