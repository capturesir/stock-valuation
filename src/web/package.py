"""Build the downloadable project archive (R24).

The zip must be enough to start the system from the README: source, the config
example, prompts, tests, deploy scripts and documents. Local state (fetched
case files, run output, virtualenv, secrets) is deliberately left out — it is
either reproducible or private.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from .. import __version__
from ..pipeline.orchestrator import ROOT

DIST_DIR = ROOT / "dist"
EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                "out", "cases", "dist", "node_modules", ".idea", ".vscode"}
EXCLUDE_FILES = {".env", ".env.local", "config.yaml", ".DS_Store"}
EXCLUDE_SUFFIXES = (".pyc", ".tmp")


def _keep(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
        return False
    if rel.name in EXCLUDE_FILES or rel.name.endswith(EXCLUDE_SUFFIXES):
        return False
    # Keep the delivery documents small: the original request zip/docx are not
    # part of the runnable project.
    if rel.parts[0] == "docs" and rel.suffix.lower() in {".zip", ".docx"}:
        return False
    return True


def build_package() -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    target = DIST_DIR / f"stock-valuation-v{__version__}.zip"
    tmp = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ROOT.rglob("*")):
            if path.is_dir() or not _keep(path):
                continue
            zf.write(path, arcname=Path(f"stock-valuation-v{__version__}") / path.relative_to(ROOT))
    tmp.replace(target)
    return target
