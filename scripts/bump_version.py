#!/usr/bin/env python3
"""Set the release version everywhere it appears (VERSION is the single source).

    python scripts/bump_version.py 2.0.0-alpha.2
    python scripts/bump_version.py --check        # exit 1 if any file disagrees with VERSION

Updates: VERSION, every frontend package.json, .env.example (APP_VERSION) and the
default image tags in docker-compose.yml, then adds an empty CHANGELOG section.
The API path version (/api/v1) is separate and only changes on breaking API changes;
see docs/versioning.md.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:alpha|beta|rc)\.(?:0|[1-9]\d*)))?$")


def package_jsons() -> list[Path]:
    return sorted(p for p in (ROOT / "frontend").rglob("package.json") if "node_modules" not in p.parts)


def current() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def check() -> int:
    v, bad = current(), []
    for p in package_jsons():
        if json.loads(p.read_text(encoding="utf-8")).get("version") != v:
            bad.append(str(p.relative_to(ROOT)))
    if f"APP_VERSION={v}" not in (ROOT / ".env.example").read_text(encoding="utf-8"):
        bad.append(".env.example")
    if f"${{APP_VERSION:-{v}}}" not in (ROOT / "docker-compose.yml").read_text(encoding="utf-8"):
        bad.append("docker-compose.yml")
    if bad:
        print(f"VERSION is {v}, but these disagree: {', '.join(bad)}")
        return 1
    print(f"all files at {v}")
    return 0


def bump(new: str) -> int:
    if not SEMVER.match(new):
        print(f"not a valid version: {new!r} (expected e.g. 2.0.1, 2.1.0-beta.1)")
        return 2
    old = current()
    (ROOT / "VERSION").write_text(new + "\n", encoding="utf-8")
    for p in package_jsons():
        data = json.loads(p.read_text(encoding="utf-8"))
        data["version"] = new
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    env = ROOT / ".env.example"
    env.write_text(re.sub(r"^APP_VERSION=.*$", f"APP_VERSION={new}", env.read_text(encoding="utf-8"), flags=re.M), encoding="utf-8")
    comp = ROOT / "docker-compose.yml"
    comp.write_text(comp.read_text(encoding="utf-8").replace(f"${{APP_VERSION:-{old}}}", f"${{APP_VERSION:-{new}}}"), encoding="utf-8")
    log = ROOT / "CHANGELOG.md"
    text = log.read_text(encoding="utf-8")
    if f"## {new}" not in text:
        stub = f"## {new} — {dt.date.today().isoformat()}\n\n### 新增\n\n### 修正\n\n---\n\n"
        text = text.replace("---\n\n", "---\n\n" + stub, 1)
        log.write_text(text, encoding="utf-8")
    print(f"{old} → {new}. Next: fill in CHANGELOG.md, commit, then `git tag v{new}`.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(check() if sys.argv[1] == "--check" else bump(sys.argv[1]))
