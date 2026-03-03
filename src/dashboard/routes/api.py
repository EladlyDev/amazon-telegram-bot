"""REST API endpoints — JSON responses consumed by the dashboard frontend.

All endpoints require authentication via the session cookie unless noted.
Access shared services through ``request.app.state``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import telegram
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.status import HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR

from src.dashboard.auth import (
    SESSION_COOKIE_NAME,
    get_client_ip,
    session_signer,
    verify_password,
)
from src.dashboard.deps import get_current_user
from src.dashboard.otp_store import pending_changes
from src.dashboard.rate_limit import recovery_limiter
from src.dashboard.security import (
    classify_setting,
    format_change_confirmation,
    format_otp_message,
    get_otp_chat_id,
    separate_changes,
)

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
#  HEALTH
# ────────────────────────────────────────────────────────────


@router.get("/health")
async def health():
    """Minimal health check (no auth required)."""
    return {"status": "ok"}


@router.get("/health/full")
async def health_full(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Detailed health info (requires auth)."""
    repo = request.app.state.repo
    scheduler = request.app.state.scheduler
    stats = await repo.get_dashboard_stats()
    return {
        "status": "ok",
        "bot_running": stats.get("bot_is_running", False),
        "scheduler_running": scheduler.is_running,
        "total_published": stats.get("total_published", 0),
        "today_count": stats.get("today_count", 0),
    }


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

    # Strip non-updatable fields to avoid passing strings to DateTime columns
    for key in ("id", "created_at", "updated_at", "keywords"):
        body.pop(key, None)

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
#  SETTINGS (with OTP protection for sensitive changes)
# ────────────────────────────────────────────────────────────


@router.get("/settings")
async def get_settings(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Return all settings grouped by group_name, each with sensitivity info."""
    repo = request.app.state.repo
    all_settings = await repo.get_all_settings()

    grouped: dict[str, list] = {}
    for s in all_settings:
        group = s.group_name or "general"
        data = _serialize(s)
        data["sensitivity"] = classify_setting(s.key)
        grouped.setdefault(group, []).append(data)

    otp_chat_id = await get_otp_chat_id(repo)
    return {
        "settings": grouped,
        "otp_configured": bool(otp_chat_id),
        "otp_target": (otp_chat_id[:4] + "***") if otp_chat_id else None,
    }


@router.put("/settings")
async def update_settings(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Smart save: normal settings immediately, sensitive via OTP."""
    repo = request.app.state.repo
    notifier = getattr(request.app.state, "security_notifier", None)
    body = await request.json()

    # Build current values
    all_settings = await repo.get_all_settings()
    current_values = {s.key: s.value for s in all_settings}

    normal_changes, sensitive_changes = separate_changes(current_values, body)

    # Save normal changes immediately
    if normal_changes:
        await repo.update_settings_bulk(normal_changes)
        await repo.log_event(
            "INFO", "dashboard", "settings_updated",
            f"Updated {len(normal_changes)} normal settings.",
        )

    # If no sensitive changes, done
    if not sensitive_changes:
        return {"status": "ok", "saved": len(normal_changes)}

    # ── Sensitive changes need OTP ──────────────────────────
    otp_chat_id = await get_otp_chat_id(repo)

    # First-run: no admin chat ID configured → save directly
    if not otp_chat_id:
        await repo.update_settings_bulk(sensitive_changes)
        if notifier:
            await notifier.on_sensitive_settings_changed(
                sensitive_changes, current_values, user["username"], False
            )
        await repo.log_event(
            "WARNING", "security", "settings_sensitive_no_otp",
            f"Saved {len(sensitive_changes)} sensitive settings without OTP (no admin chat ID).",
        )
        return {
            "status": "ok",
            "saved": len(normal_changes) + len(sensitive_changes),
            "warning": "تم حفظ الإعدادات الحساسة بدون رمز تحقق (لم يتم تعيين معرّف المسؤول)",
        }

    # Store pending + generate OTP
    pending_changes.store_settings_change(
        user["user_id"], sensitive_changes, current_values
    )
    code = await repo.create_otp(user["user_id"], "settings_change")

    # Send OTP via Telegram
    otp_sent = False
    try:
        bot_token = getattr(request.app.state, "settings", None)
        token = bot_token.telegram_bot_token if bot_token else ""
        if token:
            bot = telegram.Bot(token=token)
            msg = format_otp_message(code, sensitive_changes, current_values)
            await bot.send_message(
                chat_id=otp_chat_id,
                text=msg,
                parse_mode=telegram.constants.ParseMode.HTML,
            )
            otp_sent = True
    except Exception as exc:
        logger.error("Failed to send settings OTP: %s", exc)

    if notifier:
        await notifier.on_otp_requested(user["username"], "settings_change")

    if not otp_sent:
        # Telegram failed → save directly with warning
        pending_changes.retrieve(user["user_id"], "settings_change")  # consume
        await repo.update_settings_bulk(sensitive_changes)
        if notifier:
            await notifier.on_sensitive_settings_changed(
                sensitive_changes, current_values, user["username"], False
            )
        await repo.log_event(
            "WARNING", "security", "settings_otp_send_failed",
            "Saved sensitive settings directly — OTP delivery failed.",
        )
        return {
            "status": "ok",
            "saved": len(normal_changes) + len(sensitive_changes),
            "warning": "تعذر إرسال رمز التحقق — تم حفظ الإعدادات مباشرة",
        }

    return {
        "status": "otp_required",
        "otp_required": True,
        "saved_normal": len(normal_changes),
        "pending_sensitive": list(sensitive_changes.keys()),
    }


# ────────────────────────────────────────────────────────────
#  UNIVERSAL OTP VERIFICATION
# ────────────────────────────────────────────────────────────


@router.post("/verify-otp")
async def verify_otp(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Verify an OTP and apply the pending change."""
    repo = request.app.state.repo
    notifier = getattr(request.app.state, "security_notifier", None)
    body = await request.json()

    code = body.get("code", "").strip()
    purpose = body.get("purpose", "").strip()

    if not code or not purpose:
        raise HTTPException(status_code=400, detail="الرمز والغرض مطلوبان")

    # Verify OTP in DB
    valid = await repo.verify_otp(user["user_id"], code, purpose)
    if not valid:
        raise HTTPException(status_code=400, detail="رمز التحقق غير صحيح أو منتهي الصلاحية")

    # Retrieve pending change
    pending = pending_changes.retrieve(user["user_id"], purpose)
    if not pending:
        raise HTTPException(
            status_code=400,
            detail="لا توجد تغييرات معلقة أو انتهت صلاحيتها",
        )

    # ── Apply based on purpose ──────────────────────────────
    if purpose == "settings_change":
        changes = pending["changes"]
        old_values = pending["current_values"]
        await repo.update_settings_bulk(changes)

        if notifier:
            await notifier.on_sensitive_settings_changed(
                changes, old_values, user["username"], True
            )
            # Special: admin chat ID changed
            if "telegram.admin_chat_id" in changes:
                old_id = old_values.get("telegram.admin_chat_id", "")
                new_id = changes["telegram.admin_chat_id"]
                await notifier.on_admin_chat_id_changed(
                    old_id, new_id, user["username"]
                )

        await repo.log_event(
            "WARNING", "security", "settings_sensitive_changed",
            f"Sensitive settings changed via OTP by {user['username']}: "
            + ", ".join(changes.keys()),
        )
        return {"status": "ok", "message": "تم حفظ الإعدادات بنجاح"}

    elif purpose == "password_change":
        new_password = pending["new_password"]
        await repo.update_user_password(user["user_id"], new_password)
        if notifier:
            await notifier.on_password_changed(user["username"], "dashboard")
        await repo.log_event(
            "WARNING", "security", "password_changed",
            f"Password changed via OTP by {user['username']}.",
        )
        return {"status": "ok", "message": "تم تغيير كلمة المرور بنجاح"}

    elif purpose == "username_change":
        new_username = pending["new_username"]
        try:
            await repo.update_user_profile(
                user["user_id"], {"username": new_username}
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="اسم المستخدم مستخدم بالفعل")
        await repo.log_event(
            "INFO", "security", "username_changed",
            f"Username changed from {user['username']} to {new_username}.",
        )
        return {"status": "ok", "message": "تم تغيير اسم المستخدم بنجاح"}

    raise HTTPException(status_code=400, detail="نوع العملية غير معروف")


# ────────────────────────────────────────────────────────────
#  ACCOUNT
# ────────────────────────────────────────────────────────────


@router.post("/account/request-password-change")
async def request_password_change(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Request a password change (may require OTP)."""
    repo = request.app.state.repo
    notifier = getattr(request.app.state, "security_notifier", None)
    body = await request.json()

    current_password = body.get("current_password", "")
    new_password = body.get("new_password", "")
    confirm_password = body.get("confirm_password", "")

    # Validate
    db_user = await repo.get_user_by_id(user["user_id"])
    if db_user is None:
        raise HTTPException(status_code=400, detail="المستخدم غير موجود")

    if not verify_password(current_password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة")

    if len(new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="كلمة المرور الجديدة يجب أن تكون 8 أحرف على الأقل",
        )

    if new_password == current_password:
        raise HTTPException(
            status_code=400,
            detail="كلمة المرور الجديدة يجب أن تكون مختلفة عن الحالية",
        )

    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="كلمتا المرور غير متطابقتين")

    # OTP flow
    otp_chat_id = await get_otp_chat_id(repo)

    if not otp_chat_id:
        # No OTP configured → change directly
        await repo.update_user_password(user["user_id"], new_password)
        if notifier:
            await notifier.on_password_changed(user["username"], "dashboard_no_otp")
        await repo.log_event(
            "WARNING", "security", "password_changed_no_otp",
            f"Password changed without OTP by {user['username']}.",
        )
        return {"status": "ok", "message": "تم تغيير كلمة المرور بنجاح"}

    # Store pending + send OTP
    pending_changes.store_password_change(user["user_id"], new_password)
    code = await repo.create_otp(user["user_id"], "password_change")

    otp_sent = False
    try:
        bot_token = getattr(request.app.state, "settings", None)
        token = bot_token.telegram_bot_token if bot_token else ""
        if token:
            bot = telegram.Bot(token=token)
            await bot.send_message(
                chat_id=otp_chat_id,
                text=(
                    "🔐 <b>رمز التحقق لتغيير كلمة المرور</b>\n\n"
                    f"🔑 الرمز: <code>{code}</code>\n\n"
                    "⏰ صالح لمدة 5 دقائق\n"
                    "⚠️ لا تشارك هذا الرمز مع أي شخص."
                ),
                parse_mode=telegram.constants.ParseMode.HTML,
            )
            otp_sent = True
    except Exception as exc:
        logger.error("Failed to send password OTP: %s", exc)

    if notifier:
        await notifier.on_otp_requested(user["username"], "password_change")

    if not otp_sent:
        # Telegram failed → change directly
        pending_changes.retrieve(user["user_id"], "password_change")  # consume
        await repo.update_user_password(user["user_id"], new_password)
        if notifier:
            await notifier.on_password_changed(user["username"], "dashboard_no_otp")
        await repo.log_event(
            "WARNING", "security", "password_otp_failed",
            "Changed password directly — OTP delivery failed.",
        )
        return {
            "status": "ok",
            "message": "تم تغيير كلمة المرور بنجاح",
            "warning": "تعذر إرسال رمز التحقق",
        }

    return {"status": "otp_required", "otp_required": True}


@router.put("/account/profile")
async def update_profile(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Update display name and/or username."""
    repo = request.app.state.repo
    notifier = getattr(request.app.state, "security_notifier", None)
    body = await request.json()

    update_data: dict[str, str] = {}

    if "display_name" in body:
        update_data["display_name"] = body["display_name"].strip()

    new_username = body.get("username", "").strip()
    if new_username and new_username != user["username"]:
        # Username change — allow directly but notify
        update_data["username"] = new_username

    if not update_data:
        return {"status": "ok", "message": "لا توجد تغييرات"}

    try:
        updated = await repo.update_user_profile(user["user_id"], update_data)
    except ValueError:
        raise HTTPException(status_code=400, detail="اسم المستخدم مستخدم بالفعل")

    if updated is None:
        raise HTTPException(status_code=400, detail="المستخدم غير موجود")

    if "username" in update_data:
        await repo.log_event(
            "INFO", "security", "username_changed",
            f"Username changed: {user['username']} → {update_data['username']}",
        )

    return {
        "status": "ok",
        "user": {
            "username": updated.username,
            "display_name": updated.display_name,
        },
    }


# ────────────────────────────────────────────────────────────
#  SESSIONS
# ────────────────────────────────────────────────────────────


@router.get("/account/sessions")
async def get_sessions(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return active sessions for the current user."""
    repo = request.app.state.repo
    sessions = await repo.get_user_active_sessions(user["user_id"])

    return [
        {
            "id": s.id[:8] + "...",
            "full_id": s.id,
            "ip_address": s.ip_address,
            "device_info": s.device_info,
            "is_current": s.id == user["session_id"],
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_active_at": s.last_active_at.isoformat() if s.last_active_at else None,
        }
        for s in sessions
    ]


@router.post("/account/sessions/revoke-others")
async def revoke_other_sessions(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Deactivate all sessions except the current one."""
    repo = request.app.state.repo
    notifier = getattr(request.app.state, "security_notifier", None)

    count = await repo.deactivate_other_sessions(
        user["user_id"], user["session_id"]
    )

    if notifier and count > 0:
        await notifier.on_sessions_revoked(user["username"], count)

    return {"status": "ok", "revoked_count": count}


@router.post("/account/sessions/revoke/{session_id}")
async def revoke_session(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Deactivate a specific session (cannot revoke current)."""
    if session_id == user["session_id"]:
        raise HTTPException(
            status_code=400,
            detail="لا يمكنك إنهاء جلستك الحالية. استخدم تسجيل الخروج.",
        )

    repo = request.app.state.repo
    await repo.deactivate_session(session_id)
    return {"status": "ok"}


# ────────────────────────────────────────────────────────────
#  RECOVERY (no auth required)
# ────────────────────────────────────────────────────────────


@router.post("/recover")
async def recover_account(request: Request):
    """Reset password using recovery key (no auth required)."""
    repo = request.app.state.repo
    notifier = getattr(request.app.state, "security_notifier", None)
    ip = get_client_ip(request)

    # Rate limit
    if await recovery_limiter.is_rate_limited(ip, 3, 3600):
        raise HTTPException(
            status_code=429,
            detail="تم تجاوز عدد المحاولات. حاول بعد ساعة.",
        )

    body = await request.json()
    username = body.get("username", "").strip()
    recovery_key = body.get("recovery_key", "").strip()
    new_password = body.get("new_password", "")
    confirm_password = body.get("confirm_password", "")

    # Validate
    if len(new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="كلمة المرور يجب أن تكون 8 أحرف على الأقل",
        )

    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="كلمتا المرور غير متطابقتين")

    # Verify recovery key
    db_user = await repo.verify_recovery_key(username, recovery_key)

    if db_user is None:
        if notifier:
            await notifier.on_recovery_attempt(username, ip, False)
        await repo.log_event(
            "WARNING", "security", "recovery_failed",
            f"Failed recovery attempt for '{username}' from {ip}.",
        )
        raise HTTPException(status_code=400, detail="البيانات غير صحيحة")

    # Reset
    await repo.reset_user_via_recovery(db_user.id, new_password)
    await repo.deactivate_all_sessions(db_user.id)

    if notifier:
        await notifier.on_recovery_attempt(username, ip, True)

    await repo.log_event(
        "WARNING", "security", "recovery_success",
        f"Account '{username}' recovered from {ip}. All sessions revoked.",
    )

    return {"status": "ok", "message": "تم إعادة تعيين كلمة المرور بنجاح. يمكنك تسجيل الدخول الآن."}


@router.post("/recover/request-otp")
async def recover_request_otp(request: Request):
    """Send OTP for password recovery (no auth required).

    Validates that the username exists and OTP is configured,
    then sends a verification code to the admin Telegram chat.
    """
    repo = request.app.state.repo
    ip = get_client_ip(request)

    # Rate limit — stricter for unauthenticated endpoint
    if await recovery_limiter.is_rate_limited(ip, 3, 3600):
        raise HTTPException(
            status_code=429,
            detail="تم تجاوز عدد المحاولات. حاول بعد ساعة.",
        )

    body = await request.json()
    username = body.get("username", "").strip()

    if not username:
        raise HTTPException(status_code=400, detail="اسم المستخدم مطلوب")

    # Verify user exists
    db_user = await repo.get_user_by_username(username)
    if db_user is None:
        # Don't reveal whether the user exists — silently return success
        return {"status": "otp_sent"}

    # OTP must be configured
    otp_chat_id = await get_otp_chat_id(repo)
    if not otp_chat_id:
        raise HTTPException(
            status_code=400,
            detail="التحقق عبر تلقرام غير مفعّل. استخدم مفتاح الاسترداد بدلاً من ذلك.",
        )

    # Generate and send OTP
    code = await repo.create_otp(db_user.id, "recovery_otp")

    try:
        bot_token = getattr(request.app.state, "settings", None)
        token = bot_token.telegram_bot_token if bot_token else ""
        if token:
            bot = telegram.Bot(token=token)
            await bot.send_message(
                chat_id=otp_chat_id,
                text=(
                    "🔐 <b>رمز التحقق لاستعادة الحساب</b>\n\n"
                    f"👤 المستخدم: <code>{username}</code>\n"
                    f"🌐 IP: <code>{ip}</code>\n"
                    f"🔑 الرمز: <code>{code}</code>\n\n"
                    "⏰ صالح لمدة 5 دقائق\n"
                    "⚠️ إذا لم تطلب هذا الرمز، تجاهل هذه الرسالة."
                ),
                parse_mode=telegram.constants.ParseMode.HTML,
            )
    except Exception as exc:
        logger.error("Failed to send recovery OTP: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="تعذر إرسال رمز التحقق. تحقق من إعدادات البوت.",
        )

    await repo.log_event(
        "INFO", "security", "recovery_otp_requested",
        f"Recovery OTP requested for '{username}' from {ip}.",
    )
    return {"status": "otp_sent"}


@router.post("/recover/verify-otp")
async def recover_verify_otp(request: Request):
    """Verify OTP and reset password (no auth required)."""
    repo = request.app.state.repo
    notifier = getattr(request.app.state, "security_notifier", None)
    ip = get_client_ip(request)

    # Rate limit
    if await recovery_limiter.is_rate_limited(ip, 5, 3600):
        raise HTTPException(
            status_code=429,
            detail="تم تجاوز عدد المحاولات. حاول بعد ساعة.",
        )

    body = await request.json()
    username = body.get("username", "").strip()
    code = body.get("code", "").strip()
    new_password = body.get("new_password", "")
    confirm_password = body.get("confirm_password", "")

    if not username or not code:
        raise HTTPException(status_code=400, detail="جميع الحقول مطلوبة")

    if len(new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="كلمة المرور يجب أن تكون 8 أحرف على الأقل",
        )

    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="كلمتا المرور غير متطابقتين")

    # Look up user
    db_user = await repo.get_user_by_username(username)
    if db_user is None:
        raise HTTPException(status_code=400, detail="البيانات غير صحيحة")

    # Verify OTP
    valid = await repo.verify_otp(db_user.id, code, "recovery_otp")
    if not valid:
        await repo.log_event(
            "WARNING", "security", "recovery_otp_failed",
            f"Failed OTP recovery attempt for '{username}' from {ip}.",
        )
        raise HTTPException(
            status_code=400,
            detail="رمز التحقق غير صحيح أو منتهي الصلاحية",
        )

    # Reset password and deactivate all sessions
    await repo.update_user_password(db_user.id, new_password)
    await repo.deactivate_all_sessions(db_user.id)

    if notifier:
        await notifier.on_recovery_attempt(username, ip, True)

    await repo.log_event(
        "WARNING", "security", "recovery_otp_success",
        f"Account '{username}' recovered via OTP from {ip}. All sessions revoked.",
    )

    return {"status": "ok", "message": "تم إعادة تعيين كلمة المرور بنجاح. يمكنك تسجيل الدخول الآن."}


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
