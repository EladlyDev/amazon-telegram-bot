"""Smart security notification system.

Sends Arabic Telegram messages for security-relevant events (logins,
password changes, setting modifications, lockouts, recovery attempts).
Includes per-event-type deduplication to avoid spamming the admin.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import telegram

logger = logging.getLogger(__name__)

# Asia/Riyadh is UTC+3 (no DST)
_RIYADH_TZ = timezone(timedelta(hours=3))


class SecurityNotifier:
    """Send Telegram notifications for security events.

    Reads the admin chat ID from the **database** (not ``.env``) so
    changes take effect immediately.  Each event type has a cooldown
    period to prevent notification spam.
    """

    # Cooldown periods per event type (seconds)
    _COOLDOWN: dict[str, int] = {
        "login_success": 0,       # always (new IP)
        "login_same_ip": 3600,    # once per hour
        "login_failed": 60,       # once per minute
        "account_locked": 0,      # always
        "password_changed": 0,    # always
        "settings_changed": 0,    # always
        "otp_requested": 300,     # once per 5 min
        "recovery_attempt": 0,    # always
        "sessions_revoked": 0,    # always
        "bot_started": 0,         # always
        "bot_stopped": 0,         # always
    }

    def __init__(self, bot_token: str, repo) -> None:
        self.bot_token = bot_token
        self.repo = repo
        self._last_sent: dict[str, float] = {}

    # ────────────────────────────────────────────────────────
    #  Internal helpers
    # ────────────────────────────────────────────────────────

    async def _get_chat_id(self) -> str:
        chat_id = await self.repo.get_setting("telegram.admin_chat_id")
        return (chat_id or "").strip()

    async def _send(self, message: str, event_type: str = "general") -> bool:
        """Send *message* with cooldown check. Returns ``True`` if sent."""
        cooldown = self._COOLDOWN.get(event_type, 0)
        if cooldown > 0:
            last = self._last_sent.get(event_type, 0)
            if time.time() - last < cooldown:
                return False

        chat_id = await self._get_chat_id()
        if not chat_id or not self.bot_token:
            return False

        try:
            bot = telegram.Bot(token=self.bot_token)
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=telegram.constants.ParseMode.HTML,
            )
            self._last_sent[event_type] = time.time()
            return True
        except Exception as exc:
            logger.error("Security notification failed: %s", exc)
            return False

    def _now_riyadh(self) -> str:
        return datetime.now(_RIYADH_TZ).strftime("%Y-%m-%d %H:%M")

    # ────────────────────────────────────────────────────────
    #  Event methods
    # ────────────────────────────────────────────────────────

    async def on_login_success(
        self,
        username: str,
        ip: str,
        device: str,
        is_new_ip: bool,
    ) -> None:
        """Notify on successful login.  Full alert for new IPs only."""
        if is_new_ip:
            await self._send(
                f"🔑 <b>تسجيل دخول جديد</b>\n\n"
                f"👤 المستخدم: {username}\n"
                f"🌐 IP: <code>{ip}</code>\n"
                f"📱 الجهاز: {device}\n"
                f"⏰ {self._now_riyadh()}\n\n"
                f"ℹ️ إذا لم تكن أنت، غيّر كلمة المرور فوراً",
                event_type="login_success",
            )
        else:
            await self._send(
                f"🔑 تسجيل دخول — {username} من {ip}",
                event_type="login_same_ip",
            )

    async def on_login_failed(
        self, username: str, ip: str, attempt_count: int
    ) -> None:
        """Notify after 3+ consecutive failed login attempts."""
        if attempt_count >= 3:
            await self._send(
                f"⚠️ <b>محاولات دخول فاشلة</b>\n\n"
                f"👤 المستخدم: {username}\n"
                f"🌐 IP: <code>{ip}</code>\n"
                f"❌ عدد المحاولات: {attempt_count}\n"
                f"⏰ {self._now_riyadh()}",
                event_type="login_failed",
            )

    async def on_account_locked(self, username: str, ip: str) -> None:
        """Notify when an account is locked due to too many failures."""
        from src.dashboard.auth import LOCKOUT_DURATION_MINUTES, LOCKOUT_THRESHOLD

        await self._send(
            f"🔒 <b>تم قفل الحساب</b>\n\n"
            f"👤 المستخدم: {username}\n"
            f"🌐 من IP: <code>{ip}</code>\n"
            f"⏱ سيُفتح بعد {LOCKOUT_DURATION_MINUTES} دقيقة\n"
            f"السبب: {LOCKOUT_THRESHOLD} محاولات دخول فاشلة",
            event_type="account_locked",
        )

    async def on_password_changed(
        self, username: str, via: str = "dashboard"
    ) -> None:
        """Notify when a password is changed.

        *via*: ``"dashboard"``, ``"recovery"``, or ``"cli"``.
        """
        method_text = {
            "dashboard": "لوحة التحكم (مع رمز تحقق)",
            "dashboard_no_otp": "لوحة التحكم (بدون رمز تحقق)",
            "recovery": "مفتاح الاسترداد",
            "cli": "أداة سطر الأوامر (SSH)",
        }.get(via, via)

        emoji = "✅" if via == "dashboard" else "⚠️"
        await self._send(
            f"{emoji} <b>تم تغيير كلمة المرور</b>\n\n"
            f"👤 المستخدم: {username}\n"
            f"🔧 الطريقة: {method_text}\n"
            f"⏰ {self._now_riyadh()}",
            event_type="password_changed",
        )

    async def on_sensitive_settings_changed(
        self,
        changes: dict[str, str],
        old_values: dict[str, str],
        username: str,
        verified: bool,
    ) -> None:
        """Notify when sensitive settings are modified."""
        from src.dashboard.security import SENSITIVE_LABELS

        change_lines = []
        for key, new_val in changes.items():
            label = SENSITIVE_LABELS.get(key, key)
            old_val = old_values.get(key, "(فارغ)") or "(فارغ)"
            change_lines.append(
                f"  • {label}: <code>{old_val}</code> → <code>{new_val}</code>"
            )

        verified_text = "✅ مع رمز تحقق" if verified else "⚠️ بدون رمز تحقق"
        await self._send(
            f"⚙️ <b>تعديل إعدادات حساسة</b>\n\n"
            + "\n".join(change_lines) + "\n\n"
            f"👤 بواسطة: {username}\n"
            f"🔐 التحقق: {verified_text}\n"
            f"⏰ {self._now_riyadh()}",
            event_type="settings_changed",
        )

    async def on_otp_requested(self, username: str, purpose: str) -> None:
        """Log-level notification when an OTP is requested."""
        purpose_text = {
            "password_change": "تغيير كلمة المرور",
            "settings_change": "تعديل إعدادات حساسة",
            "username_change": "تغيير اسم المستخدم",
        }.get(purpose, purpose)

        await self._send(
            f"🔐 طلب رمز تحقق — {purpose_text} بواسطة {username}",
            event_type="otp_requested",
        )

    async def on_recovery_attempt(
        self, username: str, ip: str, success: bool
    ) -> None:
        """Notify on recovery-key usage (success or failure)."""
        if success:
            await self._send(
                f"⚠️ <b>تم استخدام مفتاح الاسترداد</b>\n\n"
                f"👤 المستخدم: {username}\n"
                f"🌐 IP: <code>{ip}</code>\n"
                f"✅ النتيجة: نجح — تم إعادة تعيين كلمة المرور\n"
                f"⏰ {self._now_riyadh()}\n\n"
                f"⚠️ إذا لم تكن أنت، حسابك في خطر!",
                event_type="recovery_attempt",
            )
        else:
            await self._send(
                f"🚨 <b>محاولة استرداد فاشلة!</b>\n\n"
                f"👤 المستخدم المستهدف: {username}\n"
                f"🌐 IP: <code>{ip}</code>\n"
                f"❌ النتيجة: مفتاح استرداد خاطئ\n"
                f"⏰ {self._now_riyadh()}",
                event_type="recovery_attempt",
            )

    async def on_sessions_revoked(self, username: str, count: int) -> None:
        """Notify when a user logs out from other sessions."""
        await self._send(
            f"🚪 <b>تسجيل خروج من أجهزة أخرى</b>\n\n"
            f"👤 المستخدم: {username}\n"
            f"📱 عدد الجلسات المنهية: {count}\n"
            f"⏰ {self._now_riyadh()}",
            event_type="sessions_revoked",
        )

    async def on_admin_chat_id_changed(
        self, old_id: str, new_id: str, username: str
    ) -> None:
        """Special handler: notify **both** old and new chat IDs."""
        if not self.bot_token:
            return

        try:
            bot = telegram.Bot(token=self.bot_token)

            if old_id:
                await bot.send_message(
                    chat_id=old_id,
                    text=(
                        f"ℹ️ <b>تم تغيير معرّف المسؤول</b>\n\n"
                        f"تم نقل إشعارات البوت إلى حساب آخر.\n"
                        f"لن تتلقى إشعارات بعد الآن.\n"
                        f"بواسطة: {username}"
                    ),
                    parse_mode=telegram.constants.ParseMode.HTML,
                )

            if new_id and new_id != old_id:
                await bot.send_message(
                    chat_id=new_id,
                    text=(
                        f"✅ <b>تم تعيينك كمسؤول للبوت</b>\n\n"
                        f"ستصلك إشعارات الأخطاء ورموز التحقق هنا.\n"
                        f"⏰ {self._now_riyadh()}"
                    ),
                    parse_mode=telegram.constants.ParseMode.HTML,
                )
        except Exception as exc:
            logger.error("Admin chat-ID change notification failed: %s", exc)


# ── Module-level instance (initialised in main.py) ─────────

security_notifier: SecurityNotifier | None = None
