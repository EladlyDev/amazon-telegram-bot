"""HTML page routes — serves Jinja2-rendered pages for the dashboard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER

from src.config import settings
from src.dashboard.auth import (
    SESSION_COOKIE_NAME,
    session_manager,
    verify_password,
)
from src.dashboard.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])


# ────────────────────────────────────────────────────────────
#  Authentication pages
# ────────────────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the login page. Redirects to ``/`` if already authenticated."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and session_manager.verify_session(token):
        return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    templates = request.app.state.templates
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Process login form submission."""
    templates = request.app.state.templates

    if (
        username == settings.dashboard_username
        and verify_password(password, settings.dashboard_password)
    ):
        # Create session and set cookie
        token = session_manager.create_session(username)
        response = RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=86400,
        )
        logger.info("User '%s' logged in.", username)
        return response

    # Invalid credentials
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": "اسم المستخدم أو كلمة المرور غير صحيحة",
        },
    )


@router.get("/logout")
async def logout():
    """Delete the session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response


# ────────────────────────────────────────────────────────────
#  Dashboard pages (all require auth)
# ────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Main dashboard overview page."""
    import json as _json
    from datetime import datetime as _dt, timedelta as _td

    repo = request.app.state.repo
    scheduler = request.app.state.scheduler

    stats = await repo.get_dashboard_stats()

    # Always compute next_run from DB schedule (APScheduler cache gets stale)
    next_run = None
    schedule_active = False
    schedule = await repo.get_active_schedule()
    if schedule and schedule.is_active:
        schedule_active = True
        import pytz as _pytz
        tz = _pytz.timezone(schedule.timezone or "Asia/Riyadh")
        now = _dt.now(tz)

        if schedule.schedule_type == "fixed_times":
            try:
                times = _json.loads(schedule.fixed_times or "[]")
                for t in sorted(times):
                    h, m = map(int, t.split(":"))
                    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    if candidate > now:
                        next_run = t
                        break
                if not next_run and times:
                    next_run = sorted(times)[0] + " (غداً)"
            except Exception:
                pass
        elif schedule.schedule_type == "interval":
            mins = schedule.interval_minutes or 180
            next_time = now + _td(minutes=mins)
            next_run = next_time.strftime("%H:%M")

    recent_logs = await repo.get_logs(limit=10)
    daily_stats = await repo.get_daily_stats(days=7)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "next_run": next_run,
            "schedule_active": schedule_active or scheduler.is_running,
            "recent_logs": recent_logs,
            "daily_stats": daily_stats,
        },
    )


@router.get("/categories", response_class=HTMLResponse)
async def categories_page(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Category management page."""
    repo = request.app.state.repo
    categories = await repo.get_all_categories(include_inactive=True)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "categories.html",
        {
            "request": request,
            "user": user,
            "categories": categories,
        },
    )


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Schedule configuration page."""
    repo = request.app.state.repo
    schedule = await repo.get_schedule()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "schedule.html",
        {
            "request": request,
            "user": user,
            "schedule": schedule,
        },
    )


@router.get("/templates", response_class=HTMLResponse)
async def templates_page(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Post template management page."""
    repo = request.app.state.repo
    all_templates = await repo.get_all_templates()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "templates.html",
        {
            "request": request,
            "user": user,
            "post_templates": all_templates,
        },
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    user: dict = Depends(get_current_user),
    page: int = 1,
    per_page: int = 50,
    level: str | None = None,
    component: str | None = None,
):
    """System logs page with pagination and filters."""
    repo = request.app.state.repo
    offset = (page - 1) * per_page
    logs = await repo.get_logs(
        limit=per_page, offset=offset, level=level, component=component
    )
    total = await repo.get_logs_count(level=level, component=component)
    total_pages = max(1, (total + per_page - 1) // per_page)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "user": user,
            "logs": logs,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "level": level,
            "component": component,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Bot settings page."""
    repo = request.app.state.repo
    all_settings = await repo.get_all_settings()

    # Group by group_name
    grouped: dict[str, list] = {}
    for s in all_settings:
        group = s.group_name or "general"
        grouped.setdefault(group, []).append(s)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "settings_groups": grouped,
        },
    )
