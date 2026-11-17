"""API token check and at-rest encryption of the user's AI key."""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Header, Query

from .config import get_settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # APP_SECRET_KEY changed: the stored key can no longer be read. Treat as unset.
        return None


def mask(key: Optional[str]) -> dict:
    if not key:
        return {"set": False, "length": 0, "tail": None}
    return {"set": True, "length": len(key), "tail": key[-4:] if len(key) >= 4 else key}


def require_token(authorization: Optional[str] = Header(default=None),
                  access_token: Optional[str] = Query(default=None),
                  accept_language: Optional[str] = Header(default=None)) -> None:
    """Bearer token when APP_API_TOKEN is set. ``access_token`` is accepted for SSE,
    because EventSource in browsers cannot send headers."""
    expected = get_settings().api_token
    if not expected:
        return
    given = None
    if authorization and authorization.lower().startswith("bearer "):
        given = authorization[7:].strip()
    given = given or access_token
    if not given or not hmac.compare_digest(given, expected):
        from .i18n import http_error, pick
        raise http_error(401, "unauthorized", pick(accept_language))
