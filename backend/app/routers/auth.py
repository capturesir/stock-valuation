"""Sign-in, sign-out, the signed-in user's profile and preferences, and user management (admin)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth
from ..auth import Principal, current_principal, require_admin, require_user
from ..db import get_db
from ..i18n import bad_request, http_error, lang_dep
from ..models import User
from ..schemas import (AdminUserIn, AdminUserUpdate, AuthStatus, LoginIn, LoginOut, MeIn, PasswordIn, Prefs,
                       UserOut)
from .common import iso

public = APIRouter(tags=["auth"])
me_router = APIRouter(tags=["auth"])
admin = APIRouter(tags=["admin"])


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role,
            "is_active": u.is_active, "prefs": Prefs(**(u.prefs or {})).model_dump(),
            "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at) if u.last_login_at else None}


# ------------------------------------------------------------------ public

@public.get("/auth/status", response_model=AuthStatus, summary="是否尚未建立任何帳號（登入頁用）")
def status(db: Session = Depends(get_db)) -> dict:
    return {"needs_setup": (db.scalar(select(func.count()).select_from(User)) or 0) == 0}


@public.post("/auth/login", response_model=LoginOut, summary="登入（網頁設定 Cookie；App 可要求令牌）")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db),
          lang: str = Depends(lang_dep)) -> dict:
    user, err = auth.authenticate(db, body.email, body.password)
    if err:
        db.commit()                       # keep the failed-attempt counter even though we raise
        raise http_error(429 if err == "account_locked" else 401, err, lang)
    kind = "token" if body.issue_token else "cookie"
    token = auth.start_session(db, user, kind=kind, user_agent=request.headers.get("user-agent"))
    if body.issue_token:
        return {"user": user_out(user), "token": token}
    auth.set_cookie(response, request, token)
    return {"user": user_out(user), "token": None}


@public.post("/auth/logout", status_code=204, summary="登出此裝置")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    authz = request.headers.get("authorization") or ""
    token = authz[7:].strip() if authz.lower().startswith("bearer ") else request.cookies.get(auth.COOKIE)
    auth.end_session(db, token)
    auth.clear_cookie(response)


# ------------------------------------------------------------------ the signed-in user

@me_router.get("/auth/me", response_model=UserOut, summary="目前登入的用戶")
def me(user: User = Depends(require_user)) -> dict:
    return user_out(user)


@me_router.put("/auth/me", response_model=UserOut, summary="更改顯示名稱與偏好設定")
def update_me(body: MeIn, user: User = Depends(require_user)) -> dict:
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.prefs is not None:
        user.prefs = body.prefs.model_dump()
    return user_out(user)


@me_router.put("/auth/me/password", status_code=204, summary="更改密碼（其他裝置會被登出）")
def change_password(body: PasswordIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                    lang: str = Depends(lang_dep)) -> None:
    if p.user is None:
        raise http_error(403, "user_required", lang)
    if not auth.verify_password(p.user, body.current_password):
        raise http_error(400, "wrong_password", lang)
    try:
        p.user.password_hash = auth.hash_password(body.new_password)
    except ValueError as exc:
        raise bad_request(exc, lang)
    auth.revoke_sessions(db, p.user.id, keep=p.session_hash)


# ------------------------------------------------------------------ user management (admin)

@admin.get("/admin/users", response_model=list[UserOut], summary="全部用戶")
def list_users(_: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    return [user_out(u) for u in db.scalars(select(User).order_by(User.created_at))]


@admin.post("/admin/users", response_model=UserOut, status_code=201, summary="新增用戶")
def add_user(body: AdminUserIn, _: Principal = Depends(require_admin), db: Session = Depends(get_db),
             lang: str = Depends(lang_dep)) -> dict:
    try:
        u = auth.create_user(db, email=body.email, display_name=body.display_name, password=body.password,
                             role=body.role)
    except ValueError as exc:
        raise bad_request(exc, lang)
    return user_out(u)


@admin.put("/admin/users/{user_id}", response_model=UserOut, summary="修改用戶：名稱、角色、啟用、重設密碼")
def update_user(user_id: str, body: AdminUserUpdate, p: Principal = Depends(require_admin),
                db: Session = Depends(get_db), lang: str = Depends(lang_dep)) -> dict:
    u = db.get(User, user_id)
    if u is None:
        raise http_error(404, "user_not_found", lang)
    losing_admin = u.role == "admin" and u.is_active and (
        (body.role is not None and body.role != "admin") or body.is_active is False)
    if losing_admin and auth.active_admins(db) <= 1:
        raise http_error(400, "last_admin", lang)
    if body.display_name is not None:
        u.display_name = body.display_name.strip()
    if body.role is not None:
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
        if not body.is_active:
            auth.revoke_sessions(db, u.id)
    if body.password:
        try:
            u.password_hash = auth.hash_password(body.password)
        except ValueError as exc:
            raise bad_request(exc, lang)
        u.failed_logins, u.locked_until = 0, None
        auth.revoke_sessions(db, u.id, keep=p.session_hash if p.user and p.user.id == u.id else None)
    return user_out(u)
