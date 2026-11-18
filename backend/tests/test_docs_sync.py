"""docs/資料庫設計.md and docs/接口文件.md must match the code (owner's rule, 2026-11-18).

A new table, column or endpoint without its documentation fails CI, and so does documentation
for something that no longer exists.
"""
from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
ROW = re.compile(r"^\| (GET|POST|PUT|DELETE|PATCH) \| `([^`?]+)", re.M)


def _sections(text: str) -> dict[str, str]:
    """Table name -> its section, for headings like ### `users`."""
    out, name, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^### `([a-z_]+)`\s*$", line)
        if m or line.startswith("## ") or (line.startswith("### ") and name):
            if name:
                out[name] = "\n".join(buf)
            name, buf = (m.group(1) if m else None), []
            continue
        if name:
            buf.append(line)
    if name:
        out[name] = "\n".join(buf)
    return out


def test_database_doc_lists_every_table_and_column():
    from backend.app.db import Base
    from backend.app import models  # noqa: F401  (registers the tables)

    sections = _sections((DOCS / "資料庫設計.md").read_text(encoding="utf-8"))
    tables = Base.metadata.tables
    missing = []
    for name, table in tables.items():
        sec = sections.get(name)
        if sec is None:
            missing.append(f"table {name}")
            continue
        columns = sec.split("| 欄位 |", 1)[-1].split("\n\n", 1)[0]     # the column table only
        documented = set(re.findall(r"^\| `([a-z_0-9]+)` \|", columns, re.M))
        missing += [f"{name}.{c.name}" for c in table.columns if c.name not in documented]
        missing += [f"{name}.{c} (no longer exists)" for c in documented - {c.name for c in table.columns}]
    missing += [f"table {s} (no longer exists)" for s in sections if s not in tables]
    assert not missing, "docs/資料庫設計.md is out of date: " + ", ".join(missing)


def test_api_doc_lists_every_endpoint(client):
    spec = client.app.openapi()
    actual = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops}
    text = (DOCS / "接口文件.md").read_text(encoding="utf-8")
    listed = text.split("## 3.")[0]          # section 3 is planned endpoints
    documented = set(ROW.findall(listed))
    assert not actual - documented, f"docs/接口文件.md is missing: {sorted(actual - documented)}"
    assert not documented - actual, f"docs/接口文件.md lists endpoints that do not exist: {sorted(documented - actual)}"
