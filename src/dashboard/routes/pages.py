"""HTML page routes — serves Jinja2-rendered pages for the dashboard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER

from src.dashboard.auth import (
    LOCKOUT_THRESHOLD,
    SESSION_COOKIE_NAME,
    get_client_ip,
    session_signer,
    verify_password,
)
from src.dashboard.deps import get_current_user
from src.dashboard.rate_limit import login_limiter
from src.dashboard.security import get_otp_chat_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])


# ────────────────────────────────────────────────────────────
#  Authentication pages
# ────────────────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the login page. Redirects to ``/`` if already authenticated."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session_id = session_signer.unsign_session_id(token)
        if session_id:
            repo = request.app.state.repo
            session = await repo.get_session_by_id(session_id)
            if session is not None:
                return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

    templates = request.app.state.templates
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Process login form submission with rate limiting and lockout."""
    templates = request.app.state.templates
    repo = request.app.state.repo
    ip = get_client_ip(request)
    notifier = getattr(request.app.state, "security_notifier", None)

    def _error(msg: str):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": msg}
        )

    # Rate limit
    if await login_limiter.is_rate_limited(ip, 10, 300):
        return _error("تم تجاوز عدد المحاولات. حاول بعد 5 دقائق")

    # Lockout check
    if await repo.is_user_locked(username):
        return _error("تم قفل الحساب مؤقتاً. حاول بعد 15 دقيقة")

    # User lookup
    user = await repo.get_user_by_username(username)
    if user is None or not user.is_active:
        await repo.record_login_failure(username)
        return _error("اسم المستخدم أو كلمة المرور غير صحيحة")

    # Password verification
    if not verify_password(password, user.password_hash):
        attempt_count = await repo.record_login_failure(username)
        if notifier:
            if attempt_count >= LOCKOUT_THRESHOLD:
                await notifier.on_account_locked(username, ip)
            elif attempt_count >= 3:
                await notifier.on_login_failed(username, ip, attempt_count)
        return _error("اسم المستخدم أو كلمة المرور غير صحيحة")

    # ── Success ─────────────────────────────────────────────
    await repo.record_login_success(user.id, ip)

    ua = request.headers.get("user-agent", "")
    session = await repo.create_session(user.id, ip, ua)
    signed = session_signer.sign_session_id(session.id)

    response = RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)
    is_https = request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=signed,
        httponly=True,
        samesite="lax",
        max_age=86400,
        secure=is_https,
        path="/",
    )

    if notifier:
        is_new = await repo.is_new_ip_for_user(user.id, ip)
        await notifier.on_login_success(
            user.username, ip, session.device_info or "", is_new
        )

    logger.info("User '%s' logged in from %s.", username, ip)
    return response


@router.get("/logout")
async def logout(request: Request):
    """Deactivate the session and redirect to login."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session_id = session_signer.unsign_session_id(token)
        if session_id:
            repo = request.app.state.repo
            await repo.deactivate_session(session_id)

    response = RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
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
    repo = request.app.state.repo
    scheduler = request.app.state.scheduler

    stats = await repo.get_dashboard_stats()

    # Always get next_run from the actual APScheduler job data
    next_run = None
    schedule_active = False
    schedule = await repo.get_active_schedule()
    if schedule and schedule.is_active:
        schedule_active = True

    # Read next run time from APScheduler (the source of truth)
    next_run = scheduler.get_next_run_time()

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
    """Bot settings page with OTP status."""
    repo = request.app.state.repo
    all_settings = await repo.get_all_settings()

    # Group by group_name
    grouped: dict[str, list] = {}
    for s in all_settings:
        group = s.group_name or "general"
        grouped.setdefault(group, []).append(s)

    otp_chat_id = await get_otp_chat_id(repo)
    otp_configured = bool(otp_chat_id)
    otp_target = (otp_chat_id[:4] + "***") if otp_chat_id else None

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "settings_groups": grouped,
            "otp_configured": otp_configured,
            "otp_target": otp_target,
        },
    )


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """User account page with profile info and active sessions."""
    repo = request.app.state.repo
    db_user = await repo.get_user_by_id(user["user_id"])
    sessions = await repo.get_user_active_sessions(user["user_id"])

    otp_chat_id = await get_otp_chat_id(repo)
    otp_configured = bool(otp_chat_id)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "user": user,
            "db_user": db_user,
            "sessions": sessions,
            "otp_configured": otp_configured,
        },
    )


@router.get("/recover", response_class=HTMLResponse)
async def recover_page(request: Request):
    """Account recovery page (does NOT require auth)."""
    repo = request.app.state.repo
    otp_chat_id = await get_otp_chat_id(repo)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "recover.html",
        {"request": request, "otp_configured": bool(otp_chat_id)},
    )
