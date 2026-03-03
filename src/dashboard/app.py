"""FastAPI application factory for the dashboard.

Creates a fully configured FastAPI instance with:
- Static file serving
- Jinja2 template engine
- Shared state (repository, scheduler, engine)
- Security middleware
- Route routers (mounted by the factory)
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.database.repository import Repository
from src.engine.core import BotEngine
from src.engine.scheduler import PublishScheduler

logger = logging.getLogger(__name__)

# Resolve paths relative to this package, not the working directory
_PACKAGE_DIR = Path(__file__).parent
_STATIC_DIR = _PACKAGE_DIR / "static"
_TEMPLATE_DIR = _PACKAGE_DIR / "templates"


def create_dashboard_app(
    repository: Repository,
    scheduler: PublishScheduler,
    engine: BotEngine,
    security_notifier=None,
    app_settings=None,
) -> FastAPI:
    """Create and configure the FastAPI dashboard application.

    Args:
        repository: Database access layer.
        scheduler: APScheduler wrapper for publish jobs.
        engine: Main bot orchestration engine.
        security_notifier: Telegram security notifier for OTP / alerts.
        app_settings: Application settings object.

    Returns:
        A fully configured :class:`FastAPI` instance.
    """
    app = FastAPI(
        title="Amazon Bot Dashboard",
        description="Dashboard for managing the Amazon-to-Telegram bot.",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # ── Security middleware ──────────────────────────────────
    from src.dashboard.middleware import (
        RequestLoggingMiddleware,
        SecurityHeadersMiddleware,
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    # ── Ensure directories exist ────────────────────────────
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    _TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    # ── Static files ────────────────────────────────────────
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── Templates ───────────────────────────────────────────
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

    # Add a filter to convert UTC datetimes to local time for display
    from datetime import datetime as _dt, timezone as _tz

    def _localtime(utc_val, fmt: str = "%H:%M:%S") -> str:
        """Convert a naive UTC datetime (or ISO string) to the server's local time."""
        if not utc_val:
            return "—"
        if isinstance(utc_val, str):
            try:
                utc_val = _dt.fromisoformat(utc_val.replace("Z", "+00:00"))
                if utc_val.tzinfo:
                    return utc_val.astimezone().strftime(fmt)
                utc_val = utc_val.replace(tzinfo=_tz.utc)
                return utc_val.astimezone().strftime(fmt)
            except (ValueError, TypeError):
                return str(utc_val)
        aware = utc_val.replace(tzinfo=_tz.utc)
        local = aware.astimezone()
        return local.strftime(fmt)

    templates.env.filters["localtime"] = _localtime

    # ── Shared state ────────────────────────────────────────
    app.state.repo = repository
    app.state.scheduler = scheduler
    app.state.engine = engine
    app.state.templates = templates
    app.state.security_notifier = security_notifier
    app.state.settings = app_settings

    # ── Include routers ─────────────────────────────────────
    from src.dashboard.routes.pages import router as pages_router
    from src.dashboard.routes.api import router as api_router

    app.include_router(pages_router)
    app.include_router(api_router, prefix="/api")

    # ── Exception handlers ──────────────────────────────────

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc):  # noqa: ANN001
        """Redirect unknown routes to the dashboard."""
        # API routes get a JSON 404
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=404,
                content={"detail": "Not found"},
            )
        # Everything else redirects to dashboard home
        return RedirectResponse(url="/")

    logger.info("Dashboard app created.")
    return app
