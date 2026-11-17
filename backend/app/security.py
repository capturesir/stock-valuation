"""At-rest encryption of the AI key (sign-in and the API token live in auth.py)."""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

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
