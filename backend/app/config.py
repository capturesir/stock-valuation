"""Settings from environment variables (12-factor; Docker sets them in .env)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import ROOT


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return default if v is None or not v.strip() else v.strip()


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    redis_url: Optional[str]          # None: in-process jobs (local dev, tests)
    api_token: Optional[str]          # None: no auth (bound to localhost only)
    secret_key: str                   # Fernet key material for the stored AI key
    cors_origins: list[str] = field(default_factory=list)
    import_legacy: bool = True        # import out/ valuations into the DB on start
    timezone: str = "Asia/Macau"


def _secret(data_dir: Path) -> str:
    """APP_SECRET_KEY, or a random key created once and kept in data/ (0600)."""
    v = _env("APP_SECRET_KEY")
    if v:
        return v
    path = data_dir / ".secret_key"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(32)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _database_url() -> str:
    """DATABASE_URL, or a local PostgreSQL built from the same POSTGRES_* values Docker uses.
    PostgreSQL is the only supported database (decision D3): dev, tests and production alike."""
    url = _env("DATABASE_URL")
    if url is None:
        user, pw = _env("POSTGRES_USER", "valuation"), _env("POSTGRES_PASSWORD", "change-me")
        host, port = _env("POSTGRES_HOST", "localhost"), _env("POSTGRES_PORT", "5432")
        url = f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{_env('POSTGRES_DB', 'valuation')}"
    if not url.startswith("postgresql"):
        raise RuntimeError(f"只支援 PostgreSQL，DATABASE_URL 必須以 postgresql 開頭（目前為 {url.split(':', 1)[0]}）。")
    return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # var/ (git-ignored) holds the generated secret key and uploads; data/ is versioned source data
    data_dir = Path(_env("APP_DATA_DIR") or (ROOT / "var"))
    data_dir.mkdir(parents=True, exist_ok=True)
    origins = [o.strip() for o in (_env("CORS_ORIGINS", "") or "").split(",") if o.strip()]
    return Settings(
        data_dir=data_dir,
        database_url=_database_url(),
        redis_url=_env("REDIS_URL"),
        api_token=_env("APP_API_TOKEN"),
        secret_key=_secret(data_dir),
        cors_origins=origins or ["http://localhost:5173", "http://127.0.0.1:5173"],
        import_legacy=_bool("APP_IMPORT_LEGACY", True),
        timezone=_env("APP_TIMEZONE", "Asia/Macau") or "Asia/Macau",
    )


def reset_settings_cache() -> None:
    """Tests change env vars between cases."""
    get_settings.cache_clear()
