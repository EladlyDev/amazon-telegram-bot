"""Security classification and OTP helpers for dashboard settings.

Separates settings into *sensitive* (require OTP), *readonly* (system-
managed), and *normal* (free to edit).  Also provides formatting for
OTP and confirmation messages sent via Telegram.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Asia/Riyadh is UTC+3 (no DST)
_RIYADH_TZ = timezone(timedelta(hours=3))


# ── Setting classification ─────────────────────────────────

SENSITIVE_SETTINGS: set[str] = {
    "telegram.admin_chat_id",
    "telegram.channel_id",
    "amazon.partner_tag",
}

READONLY_SETTINGS: set[str] = {
    "amazon.marketplace",
    "bot.is_running",
    "bot.last_publish_at",
    "bot.total_published",
}

SENSITIVE_LABELS: dict[str, str] = {
    "telegram.admin_chat_id": "معرّف المسؤول (تلقرام)",
    "telegram.channel_id": "معرّف القناة",
    "amazon.partner_tag": "رمز التتبع (Partner Tag)",
}


def classify_setting(key: str) -> str:
    """Return ``'sensitive'``, ``'readonly'``, or ``'normal'``."""
    if key in SENSITIVE_SETTINGS:
        return "sensitive"
    if key in READONLY_SETTINGS:
        return "readonly"
    return "normal"


# ── OTP chat-ID helper ─────────────────────────────────────


async def get_otp_chat_id(repo) -> str:
    """Get the Telegram chat ID for OTP delivery from the **database**.

    Returns the *current* ``telegram.admin_chat_id`` value — like
    Facebook sending a verification code to your existing phone before
    allowing a number change.  Returns an empty string if not set.
    """
    chat_id = await repo.get_setting("telegram.admin_chat_id")
    return (chat_id or "").strip()


# ── Change separation ──────────────────────────────────────


def separate_changes(
    current_values: dict[str, str],
    new_values: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Split submitted changes into ``(normal, sensitive)`` dicts.

    * Excludes *readonly* settings entirely.
    * Only includes settings whose value actually changed.
    * Strips whitespace from values.
    """
    normal: dict[str, str] = {}
    sensitive: dict[str, str] = {}

    for key, new_val in new_values.items():
        new_val = str(new_val).strip()
        old_val = str(current_values.get(key, "")).strip()

        if key in READONLY_SETTINGS:
            continue
        if new_val == old_val:
            continue

        if key in SENSITIVE_SETTINGS:
            sensitive[key] = new_val
        else:
            normal[key] = new_val

    return normal, sensitive


# ── Telegram message formatting ────────────────────────────


def format_otp_message(
    otp_code: str,
    changes: dict[str, str],
    current_values: dict[str, str],
) -> str:
    """Format the Telegram OTP message for sensitive setting changes.

    Includes: the code, a list of old → new values, expiry, and a
    warning not to share it.
    """
    from src.dashboard.auth import OTP_EXPIRY_SECONDS

    lines = [
        "🔐 <b>رمز التحقق لتعديل الإعدادات</b>",
        "",
        f"🔑 الرمز: <code>{otp_code}</code>",
        "",
        "📝 التغييرات المطلوبة:",
    ]

    for key, new_val in changes.items():
        label = SENSITIVE_LABELS.get(key, key)
        old_val = current_values.get(key, "(فارغ)") or "(فارغ)"
        new_display = new_val or "(فارغ)"
        lines.append(f"  • {label}: <code>{old_val}</code> → <code>{new_display}</code>")

    expiry_min = OTP_EXPIRY_SECONDS // 60
    lines.extend([
        "",
        f"⏰ صالح لمدة {expiry_min} دقائق",
        "",
        "⚠️ لا تشارك هذا الرمز مع أي شخص.",
    ])

    return "\n".join(lines)


def format_change_confirmation(
    changes: dict[str, str],
    current_values: dict[str, str],
    username: str,
) -> str:
    """Format the confirmation message sent **after** OTP verification."""
    now = datetime.now(_RIYADH_TZ).strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "✅ <b>تم تعديل الإعدادات بنجاح</b>",
        "",
    ]

    for key, new_val in changes.items():
        label = SENSITIVE_LABELS.get(key, key)
        old_val = current_values.get(key, "(فارغ)") or "(فارغ)"
        new_display = new_val or "(فارغ)"
        lines.append(f"  • {label}: <code>{old_val}</code> → <code>{new_display}</code>")

    lines.extend([
        "",
        f"👤 بواسطة: {username}",
        f"⏰ {now}",
    ])

    return "\n".join(lines)
