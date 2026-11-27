"""FastAPI application: /api/v1 for every frontend (web/PWA, app, mini program)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.pipeline.tickers import InvalidTicker

from . import __version__
from .config import get_settings
from .db import init_db, session
from .i18n import AppError, msg, pick
from .limits import RateLimited
from .auth import current_principal
from .routers import auth as auth_router
from .routers import branding as branding_router
from .routers import market as market_router
from .routers import ops as ops_router
from .routers import statements as statements_router
from .routers import analytics as analytics_router
from .routers import docs_qa as docs_qa_router
from .routers import settings as settings_router
from .routers import stocks, valuations, watchlist
from .schemas import Health
from .services import settings_store

log = logging.getLogger("sv.api")
API = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with session() as db:
        settings_store.ensure_default_prices(db)
        if get_settings().import_legacy:
            from .services.legacy import import_legacy
            n = import_legacy(db)
            if n:
                log.info("imported %d legacy valuations from out/", n)
        _track_tickers_in_use(db)
    from . import scheduler
    scheduler.start_inline()
    yield
    scheduler.stop_inline()


def _track_tickers_in_use(db) -> None:
    """Tickers on any watchlist or with a valuation accumulate daily prices from now on."""
    from sqlalchemy import select

    from .models import Run, WatchlistItem
    from .services import market
    from src.pipeline.tickers import normalize

    seen = set(db.scalars(select(WatchlistItem.ticker))) | set(db.scalars(select(Run.ticker).distinct()))
    for t in sorted(seen):
        try:
            if normalize(t) == t:
                market.track(db, t)
        except Exception:
            continue


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="估值工作台 API", version=__version__, lifespan=lifespan,
        description="股票估值系統 v2 後端。所有前端共用此 API。本系統產出不構成投資建議。",
        openapi_url=f"{API}/openapi.json", docs_url=f"{API}/docs", redoc_url=None,
    )
    # credentials: the web app signs in with a cookie; other origins must be listed in CORS_ORIGINS
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "Accept-Language"])

    @app.exception_handler(InvalidTicker)
    async def _bad_ticker(request: Request, exc: InvalidTicker):
        lang = pick(request.headers.get("accept-language"))
        raw = str(exc).split("「")[-1].split("」")[0] if "「" in str(exc) else ""
        return JSONResponse(status_code=400, content={"detail": {"code": "invalid_ticker",
                                                                 "error": msg("invalid_ticker", lang, ticker=raw)}})

    @app.exception_handler(RateLimited)
    async def _rate_limited(request: Request, exc: RateLimited):
        lang = pick(request.headers.get("accept-language"))
        status = 503 if exc.code == "queue_full" else 429
        return JSONResponse(status_code=status, headers={"Retry-After": str(exc.retry_after)},
                            content={"detail": {"code": exc.code, "retry_after": exc.retry_after,
                                                "error": msg(exc.code, lang, seconds=exc.retry_after, **exc.params)}})

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        lang = pick(request.headers.get("accept-language"))
        return JSONResponse(status_code=400, content={"detail": {"code": exc.code, "error": msg(exc.code, lang, **exc.params)}})

    @app.exception_handler(ValueError)
    async def _bad_value(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": {"code": "bad_request", "error": str(exc)}})

    @app.get(f"{API}/health", response_model=Health, tags=["system"], summary="服務狀態與版本")
    def health() -> dict:
        db_state = "ok"
        try:
            with session() as db:
                db.execute(text("SELECT 1"))
                ai = settings_store.public_ai_settings(db)
        except Exception as exc:  # report, don't hide
            db_state, ai = f"error: {type(exc).__name__}", {"configured": False}
        queue = "inline"
        if s.redis_url:
            try:
                import redis
                redis.Redis.from_url(s.redis_url, socket_connect_timeout=1).ping()
                queue = "redis"
            except Exception as exc:
                queue = f"error: {type(exc).__name__}"
        return {"ok": db_state == "ok" and not queue.startswith("error"), "version": __version__,
                "database": db_state, "queue": queue, "ai_configured": bool(ai.get("configured"))}

    app.include_router(auth_router.public, prefix=API)
    app.include_router(branding_router.public, prefix=API)
    secured = [Depends(current_principal)]
    for r in (auth_router.me_router, auth_router.admin, branding_router.admin, stocks.router, watchlist.router, valuations.router,
              settings_router.router, market_router.router, ops_router.router, statements_router.router, analytics_router.router,
              docs_qa_router.router):
        app.include_router(r, prefix=API, dependencies=secured)
    return app


app = create_app()
