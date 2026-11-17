"""Branding: public read (sign-in page, header, PWA manifest) and admin changes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from ..auth import Principal, require_admin
from ..db import get_db
from ..i18n import bad_request, http_error, lang_dep
from ..schemas import BrandingIn, BrandingOut
from ..services import branding

public = APIRouter(tags=["branding"])
admin = APIRouter(tags=["branding"])

# an SVG opened directly runs in this origin: forbid scripts and everything else it does not need
SVG_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
               "X-Content-Type-Options": "nosniff"}
CACHE = {"Cache-Control": "public, max-age=86400"}


@public.get("/branding", response_model=BrandingOut, summary="系統名稱與 Logo（公開，登入頁也會用）")
def get_branding(db: Session = Depends(get_db)) -> dict:
    return branding.public(db)


@public.get("/branding/logo/{variant}", summary="Logo 檔案", response_class=FileResponse)
def logo(variant: str, db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    found = branding.logo_file(db, variant) if variant in branding.VARIANTS else None
    if found is None:
        raise http_error(404, "not_found", lang, what=f"logo {variant}")
    path, media = found
    return FileResponse(path, media_type=media, headers={**CACHE, **(SVG_HEADERS if media.endswith("xml") else {})})


@public.get("/branding/icon/{name}.png", summary="由 Logo 產生的 PWA 圖示", response_class=FileResponse)
def icon(name: str, db: Session = Depends(get_db), lang: str = Depends(lang_dep)):
    path = branding.icon_file(db, name) if name in ("192", "512", "512-maskable") else None
    if path is None:
        raise http_error(404, "not_found", lang, what=f"icon {name}")
    return FileResponse(path, media_type="image/png", headers=CACHE)


@public.get("/branding/manifest.webmanifest", summary="PWA manifest（名稱與圖示跟隨品牌設定）")
def manifest(db: Session = Depends(get_db)) -> JSONResponse:
    return JSONResponse(branding.manifest(db), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})


@admin.put("/admin/branding", response_model=BrandingOut, summary="更改系統名稱（管理員）")
def put_branding(body: BrandingIn, _: Principal = Depends(require_admin), db: Session = Depends(get_db),
                 lang: str = Depends(lang_dep)) -> dict:
    try:
        return branding.set_name(db, body.name)
    except ValueError as exc:
        raise bad_request(exc, lang)


@admin.post("/admin/branding/logo/{variant}", response_model=BrandingOut,
            summary="上傳 Logo（light／dark；PNG 或 SVG，上限 512 KB；管理員）")
async def upload_logo(variant: str, file: UploadFile = File(...), _: Principal = Depends(require_admin),
                      db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    data = await file.read(branding.MAX_BYTES + 1)
    try:
        return branding.save_logo(db, variant, data, file.content_type or "")
    except ValueError as exc:
        raise bad_request(exc, lang)


@admin.delete("/admin/branding/logo/{variant}", response_model=BrandingOut, summary="移除 Logo（管理員）")
def remove_logo(variant: str, _: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return branding.delete_logo(db, variant)
