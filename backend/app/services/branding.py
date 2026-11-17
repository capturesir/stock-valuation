"""System name and logo set by an administrator (requirement 5.2).

Logos live in ``<APP_DATA_DIR>/branding/`` (the appdata volume in Docker, var/ locally), never in
Git; the database keeps the name and a content hash per logo so browsers can cache safely.
PNG and SVG only, at most 512 KB. SVGs are checked for scripts and served with a CSP that forbids
them anyway, because an SVG opened directly runs in this site's origin.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Setting

KEY = "branding"
DEFAULT_NAME = "估值工作台"
VARIANTS = ("light", "dark")
MAX_BYTES = 512 * 1024
MAX_PIXELS = 4096
ICON_SIZES = (192, 512)
SVG_FORBIDDEN = re.compile(rb"<\s*script|<\s*foreignObject|\bon[a-z]+\s*=|javascript:|<\s*iframe|<\s*embed|"
                           rb"<\s*object|(?:xlink:)?href\s*=\s*[\"']\s*(?:https?:|data:text)", re.I)


def folder() -> Path:
    d = get_settings().data_dir / "branding"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state(db: Session) -> dict:
    row = db.get(Setting, KEY)
    return dict(row.value) if row else {}


def _save(db: Session, value: dict) -> None:
    row = db.get(Setting, KEY)
    value = dict(value, updated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    if row is None:
        db.add(Setting(key=KEY, value=value))
    else:
        row.value = value
    db.flush()


def public(db: Session) -> dict:
    s = _state(db)
    logos = s.get("logos") or {}

    def url(v: str) -> Optional[str]:
        meta = logos.get(v)
        return f"/api/v1/branding/logo/{v}?v={meta['sha'][:12]}" if meta else None

    light, dark = url("light"), url("dark")
    return {"name": s.get("name") or DEFAULT_NAME, "logo_light_url": light,
            # a single logo is used for both themes
            "logo_dark_url": dark or light, "has_custom_icon": bool(logos.get("light", {}).get("icons")),
            "updated_at": s.get("updated_at")}


def set_name(db: Session, name: str) -> dict:
    name = (name or "").strip()
    if not name or len(name) > 40:
        raise ValueError("系統名稱要有 1–40 個字元。")
    s = _state(db)
    s["name"] = name
    _save(db, s)
    return public(db)


def _check_png(data: bytes):
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except Exception:
        raise ValueError("無法讀取這個 PNG 檔。")
    if max(img.size) > MAX_PIXELS:
        raise ValueError(f"圖片太大：長邊最多 {MAX_PIXELS} 像素。")
    return img


def _check_svg(data: bytes) -> None:
    head = data[:2048].lstrip().lower()
    if not (head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:4096].lower())):
        raise ValueError("這不是 SVG 檔。")
    if SVG_FORBIDDEN.search(data):
        raise ValueError("SVG 含有程式碼或外部連結，基於安全理由不能使用。")


def _icons(img, stem: str) -> list[int]:
    """Square, transparent-padded PNG icons for the PWA, plus a maskable one with safe-zone padding."""
    from PIL import Image

    img = img.convert("RGBA")
    side = max(img.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    d = folder()
    for n in ICON_SIZES:
        square.resize((n, n), Image.LANCZOS).save(d / f"{stem}-icon-{n}.png", optimize=True)
    # maskable: the logo sits inside the central 80% on a white tile (Android crops the rest)
    tile = Image.new("RGBA", (512, 512), (255, 255, 255, 255))
    inner = square.resize((410, 410), Image.LANCZOS)
    tile.paste(inner, (51, 51), inner)
    tile.save(d / f"{stem}-icon-512-maskable.png", optimize=True)
    return list(ICON_SIZES)


def save_logo(db: Session, variant: str, data: bytes, content_type: str = "") -> dict:
    if variant not in VARIANTS:
        raise ValueError("Logo 只有 light（淺色主題）和 dark（深色主題）兩種。")
    if not data:
        raise ValueError("檔案是空的。")
    if len(data) > MAX_BYTES:
        raise ValueError(f"檔案太大：上限 {MAX_BYTES // 1024} KB。")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ext, img = "png", _check_png(data)
    else:
        _check_svg(data)
        ext, img = "svg", None
    d = folder()
    for old in d.glob(f"logo-{variant}*"):
        old.unlink()
    for old in d.glob(f"{variant}-icon-*"):
        old.unlink()
    (d / f"logo-{variant}.{ext}").write_bytes(data)
    s = _state(db)
    logos = dict(s.get("logos") or {})
    logos[variant] = {"ext": ext, "sha": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                      "icons": _icons(img, variant) if img is not None else []}
    s["logos"] = logos
    _save(db, s)
    return public(db)


def delete_logo(db: Session, variant: str) -> dict:
    d = folder()
    for f in list(d.glob(f"logo-{variant}*")) + list(d.glob(f"{variant}-icon-*")):
        f.unlink()
    s = _state(db)
    logos = dict(s.get("logos") or {})
    logos.pop(variant, None)
    s["logos"] = logos
    _save(db, s)
    return public(db)


def logo_file(db: Session, variant: str) -> Optional[tuple[Path, str]]:
    meta = (_state(db).get("logos") or {}).get(variant)
    if not meta:
        return None
    path = folder() / f"logo-{variant}.{meta['ext']}"
    return (path, "image/svg+xml" if meta["ext"] == "svg" else "image/png") if path.is_file() else None


def icon_file(db: Session, name: str) -> Optional[Path]:
    """name: 192 | 512 | 512-maskable — generated from the light logo (PNG logos only)."""
    meta = (_state(db).get("logos") or {}).get("light")
    if not meta or not meta.get("icons"):
        return None
    path = folder() / f"light-icon-{name}.png"
    return path if path.is_file() else None


def manifest(db: Session) -> dict:
    b = public(db)
    s = _state(db)
    light = (s.get("logos") or {}).get("light") or {}
    v = light.get("sha", "")[:12]
    if light.get("icons"):
        icons = [{"src": f"/api/v1/branding/icon/192.png?v={v}", "sizes": "192x192", "type": "image/png"},
                 {"src": f"/api/v1/branding/icon/512.png?v={v}", "sizes": "512x512", "type": "image/png"},
                 {"src": f"/api/v1/branding/icon/512-maskable.png?v={v}", "sizes": "512x512", "type": "image/png",
                  "purpose": "maskable"}]
    elif light.get("ext") == "svg":
        icons = [{"src": b["logo_light_url"], "sizes": "any", "type": "image/svg+xml"},
                 {"src": "/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]
    else:  # the icons bundled with the web build
        icons = [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                 {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                 {"src": "/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                 {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]
    return {"name": b["name"], "short_name": b["name"][:12],
            "description": "股票估值：多方法理論價、估值走勢、財報與投資組合分析",
            "lang": "zh-Hant", "theme_color": "#1F5FBF", "background_color": "#F4F5F7",
            "display": "standalone", "start_url": "/", "scope": "/", "icons": icons}
