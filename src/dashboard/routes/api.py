"""REST API endpoints — JSON responses consumed by the dashboard frontend.

All endpoints require authentication via the session cookie.
Access shared services through ``request.app.state``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.status import HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR

from src.dashboard.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api"])


# ────────────────────────────────────────────────────────────
#  Serialization helpers
# ────────────────────────────────────────────────────────────


def _serialize(obj: Any) -> Any:
    """Recursively convert an ORM object (or list/dict) to JSON-safe dicts."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}

    # SQLAlchemy model → dict via __table__.columns
    if hasattr(obj, "__table__"):
        from sqlalchemy import inspect as sa_inspect

        data: dict[str, Any] = {}
        for col in obj.__table__.columns:
            val = getattr(obj, col.name, None)
            data[col.name] = _serialize(val)
        # Include already-loaded relationships only (avoid lazy loads)
        try:
            insp = sa_inspect(obj)
            for rel_name in insp.mapper.relationships.keys():
                if rel_name in insp.dict:  # already loaded in memory
                    data[rel_name] = _serialize(insp.dict[rel_name])
        except Exception:
            pass
        # Ensure 'keywords' key exists for Category objects
        data.setdefault("keywords", [])
        return data

    return str(obj)


def _error(message: str, status_code: int = 400) -> dict:
    """Return a standardised error dict."""
    raise HTTPException(status_code=status_code, detail=message)


# ────────────────────────────────────────────────────────────
#  BOT CONTROL
# ────────────────────────────────────────────────────────────


@router.post("/bot/start")
async def bot_start(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Start the bot (enable automatic publishing)."""
    repo = request.app.state.repo
    await repo.set_setting("bot.is_running", "true")
    await repo.log_event("INFO", "dashboard", "bot_start", "Bot started from dashboard.")
    return {"status": "ok", "message": "تم تشغيل البوت"}


@router.post("/bot/stop")
async def bot_stop(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Stop the bot (pause automatic publishing)."""
    repo = request.app.state.repo
    await repo.set_setting("bot.is_running", "false")
    await repo.log_event("INFO", "dashboard", "bot_stop", "Bot stopped from dashboard.")
    return {"status": "ok", "message": "تم إيقاف البوت"}


@router.post("/bot/trigger")
async def bot_trigger(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Manually trigger one publish cycle."""
    engine = request.app.state.engine
    try:
        result = await engine.trigger_manual()
        return result
    except Exception as exc:
        logger.exception("Manual trigger failed: %s", exc)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Trigger failed: {exc}",
        )


@router.get("/bot/status")
async def bot_status(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Return current bot and scheduler status."""
    repo = request.app.state.repo
    scheduler = request.app.state.scheduler
    return {
        "is_running": await repo.get_setting_bool("bot.is_running"),
        "scheduler_running": scheduler.is_running,
        "next_run": scheduler.get_next_run_time(),
        "jobs": scheduler.get_scheduled_jobs_info(),
    }


# ────────────────────────────────────────────────────────────
#  CATEGORIES
# ────────────────────────────────────────────────────────────


@router.get("/categories")
async def list_categories(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Return all categories with nested keywords."""
    repo = request.app.state.repo
    categories = await repo.get_all_categories(include_inactive=True)
    return _serialize(categories)


@router.post("/categories")
async def create_category(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Create a new category."""
    repo = request.app.state.repo
    body = await request.json()
    try:
        category = await repo.create_category(body)
        await repo.log_event(
            "INFO", "dashboard", "category_created",
            f"Category '{body.get('name')}' created.",
        )
        return _serialize(category)
    except Exception as exc:
        logger.exception("Failed to create category: %s", exc)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.put("/categories/{category_id}")
async def update_category(
    category_id: int,
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Update an existing category."""
    repo = request.app.state.repo
    body = await request.json()
    category = await repo.update_category(category_id, body)
    if category is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Category not found")
    return _serialize(category)


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: int,
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Delete a category and its keywords."""
    repo = request.app.state.repo
    ok = await repo.delete_category(category_id)
    if not ok:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Category not found")
    return {"status": "ok"}


# ────────────────────────────────────────────────────────────
#  KEYWORDS
# ────────────────────────────────────────────────────────────


@router.post("/categories/{category_id}/keywords")
async def create_keyword(
    category_id: int,
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Add a keyword to a category."""
    repo = request.app.state.repo
    body = await request.json()
    kw = await repo.create_keyword(
        category_id=category_id,
        keyword=body["keyword"],
        sort_order=body.get("sort_order", 0),
    )
    return _serialize(kw)


@router.put("/keywords/{keyword_id}")
async def update_keyword(
    keyword_id: int,
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Update a keyword."""
    repo = request.app.state.repo
    body = await request.json()
    kw = await repo.update_keyword(keyword_id, body)
    if kw is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Keyword not found")
    return _serialize(kw)


@router.delete("/keywords/{keyword_id}")
async def delete_keyword(
    keyword_id: int,
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Delete a keyword."""
    repo = request.app.state.repo
    ok = await repo.delete_keyword(keyword_id)
    if not ok:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Keyword not found")
    return {"status": "ok"}


# ────────────────────────────────────────────────────────────
#  SCHEDULE
# ────────────────────────────────────────────────────────────


@router.get("/schedule")
async def get_schedule(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Return the current schedule configuration."""
    repo = request.app.state.repo
    schedule = await repo.get_schedule()
    return _serialize(schedule)


@router.put("/schedule")
async def update_schedule(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Update the schedule and reload the scheduler."""
    repo = request.app.state.repo
    scheduler = request.app.state.scheduler
    body = await request.json()

    # If fixed_times is a list, convert to JSON string
    if isinstance(body.get("fixed_times"), list):
        body["fixed_times"] = json.dumps(body["fixed_times"])

    schedule = await repo.update_schedule(body)
    await scheduler.reload_schedule()

    await repo.log_event(
        "INFO", "dashboard", "schedule_updated",
        f"Schedule updated: type={body.get('schedule_type', '?')}",
    )
    return _serialize(schedule)


# ────────────────────────────────────────────────────────────
#  TEMPLATES
# ────────────────────────────────────────────────────────────


@router.get("/templates")
async def list_templates(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Return all post templates."""
    repo = request.app.state.repo
    templates = await repo.get_all_templates()
    return _serialize(templates)


@router.post("/templates")
async def create_template(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Create a new post template (inactive by default)."""
    repo = request.app.state.repo
    body = await request.json()
    body.setdefault("is_active", False)
    template = await repo.create_template(body)
    return _serialize(template)


@router.put("/templates/{template_id}")
async def update_template(
    template_id: int,
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Update an existing template."""
    repo = request.app.state.repo
    body = await request.json()
    template = await repo.update_template(template_id, body)
    if template is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Template not found")
    return _serialize(template)


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: int,
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Delete a template. Cannot delete the active template."""
    repo = request.app.state.repo
    active = await repo.get_active_template()
    if active and active.id == template_id:
        raise HTTPException(
            status_code=400,
            detail="لا يمكن حذف القالب النشط. قم بتفعيل قالب آخر أولاً.",
        )
    ok = await repo.delete_template(template_id)
    if not ok:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Template not found")
    return {"status": "ok"}


@router.post("/templates/{template_id}/activate")
async def activate_template(
    template_id: int,
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Set a template as the active one."""
    repo = request.app.state.repo
    ok = await repo.activate_template(template_id)
    if not ok:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Template not found")
    await repo.log_event(
        "INFO", "dashboard", "template_activated",
        f"Template {template_id} activated.",
    )
    return {"status": "ok"}


@router.post("/templates/preview")
async def preview_template(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Render a template with sample data and return the result."""
    from src.amazon.models import Product
    from src.engine.formatter import MessageFormatter

    body = await request.json()
    template_body = body.get("body", "")
    parse_mode = body.get("parse_mode", "HTML")

    sample = Product(
        asin="B09SAMPLE",
        title="سماعة بلوتوث لاسلكية Samsung Galaxy Buds2 Pro",
        brand="Samsung",
        original_price=599.0,
        current_price=399.0,
        currency="SAR",
        savings_percent=33.4,
        savings_amount=200.0,
        features=[
            "إلغاء ضوضاء نشط متقدم",
            "بلوتوث 5.3 مع اتصال مستقر",
            "مقاوم للماء IPX7",
        ],
        image_url="https://m.media-amazon.com/images/I/sample.jpg",
        affiliate_url="https://amazon.sa/dp/B09SAMPLE?tag=example-21",
        is_prime=True,
        rating=4.5,
        reviews_count=1234,
        category="إلكترونيات",
        is_deal=True,
        deal_badge="LIMITED_TIME_DEAL",
    )

    formatter = MessageFormatter()
    rendered = formatter.render(template_body, sample, parse_mode=parse_mode)
    return {"rendered": rendered}


# ────────────────────────────────────────────────────────────
#  SETTINGS
# ────────────────────────────────────────────────────────────


@router.get("/settings")
async def get_settings(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Return all settings grouped by group_name."""
    repo = request.app.state.repo
    all_settings = await repo.get_all_settings()

    grouped: dict[str, list] = {}
    for s in all_settings:
        group = s.group_name or "general"
        grouped.setdefault(group, []).append(_serialize(s))

    return grouped


@router.put("/settings")
async def update_settings(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Bulk-update settings from a key→value mapping."""
    repo = request.app.state.repo
    body = await request.json()
    await repo.update_settings_bulk(body)
    await repo.log_event(
        "INFO", "dashboard", "settings_updated",
        f"Updated {len(body)} settings.",
    )
    return {"status": "ok"}


# ────────────────────────────────────────────────────────────
#  STATS & LOGS
# ────────────────────────────────────────────────────────────


@router.get("/stats")
async def get_stats(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Return aggregate dashboard statistics."""
    repo = request.app.state.repo
    return await repo.get_dashboard_stats()


@router.get("/stats/daily")
async def get_daily_stats(
    request: Request,
    _user: dict = Depends(get_current_user),
    days: int = Query(default=7, ge=1, le=90),
):
    """Return per-day publish stats for the chart."""
    repo = request.app.state.repo
    return await repo.get_daily_stats(days)


@router.get("/logs")
async def get_logs(
    request: Request,
    _user: dict = Depends(get_current_user),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    level: str | None = None,
    component: str | None = None,
):
    """Return paginated system logs."""
    repo = request.app.state.repo
    offset = (page - 1) * per_page
    logs = await repo.get_logs(
        limit=per_page, offset=offset, level=level, component=component
    )
    total = await repo.get_logs_count(level=level, component=component)
    pages = max(1, (total + per_page - 1) // per_page)

    return {
        "logs": _serialize(logs),
        "total": total,
        "page": page,
        "pages": pages,
    }


@router.get("/published")
async def get_published(
    request: Request,
    _user: dict = Depends(get_current_user),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Return paginated published products."""
    repo = request.app.state.repo
    offset = (page - 1) * per_page

    # Parse optional date filters
    dt_from = _parse_date(date_from)
    dt_to = _parse_date(date_to)

    products = await repo.get_published_products(
        limit=per_page, offset=offset, status=status,
        date_from=dt_from, date_to=dt_to,
    )
    total = await repo.get_published_count()
    pages = max(1, (total + per_page - 1) // per_page)

    return {
        "products": _serialize(products),
        "total": total,
        "page": page,
        "pages": pages,
    }


def _parse_date(value: str | None) -> datetime | None:
    """Parse an ISO date string, returning ``None`` on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
