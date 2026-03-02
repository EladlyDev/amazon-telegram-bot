"""FastAPI application factory for the dashboard.

Creates a fully configured FastAPI instance with:
- Static file serving
- Jinja2 template engine
- Shared state (repository, scheduler, engine)
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

    # ── Ensure directories exist ────────────────────────────
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    _TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    # ── Static files ────────────────────────────────────────
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── Templates ───────────────────────────────────────────
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

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
