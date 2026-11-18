"""Raw layer: a fetched document, byte for byte, and where it is kept.

A document is never modified. Fetching the same content again yields the same SHA-256, so the
caller can tell "unchanged" from "the source published something new" without parsing.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
from dataclasses import dataclass, field
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class RawDocument:
    source: str                 # "sec", later "yahoo", "hkex-pdf", a paid vendor …
    kind: str                   # "company_tickers" | "submissions" | "companyfacts" …
    key: str                    # what it is about: a CIK, or "all" for a list of every company
    url: str
    content: bytes = field(repr=False)
    content_type: str = "application/json"
    http_status: int = 200
    fetched_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def sha256(self) -> str:
        return sha256(self.content)

    @property
    def size(self) -> int:
        return len(self.content)


class RawStore:
    """Content-addressed files: ``<root>/<source>/<kind>/<sha[:2]>/<sha>.gz``.

    Identical content is stored once. Files are written to a temporary name and renamed, so a
    crash never leaves a half-written document behind."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, doc: RawDocument) -> Path:
        sha = doc.sha256
        return self.root / doc.source / doc.kind / sha[:2] / f"{sha}.gz"

    def put(self, doc: RawDocument) -> Path:
        path = self.path_for(doc)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(doc.content, mtime=0))
            tmp.replace(path)
        return path

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def read(self, relative: str) -> bytes:
        data = gzip.decompress((self.root / relative).read_bytes())
        if sha256(data) != Path(relative).name.removesuffix(".gz"):
            raise ValueError(f"raw document {relative} does not match its SHA-256")
        return data
