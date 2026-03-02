"""Admin notification service for the Amazon-Telegram bot.

Sends alerts, errors, and lifecycle messages to the bot administrator
via a private Telegram chat.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import telegram
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# Asia/Riyadh is UTC+3 (no DST)
_RIYADH_TZ = timezone(timedelta(hours=3))

_LEVEL_EMOJI = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "ERROR": "❌",
}


class AdminNotifier:
    """Sends notification messages to the bot administrator."""

    def __init__(self, bot_token: str, admin_chat_id: str) -> None:
        self._bot = telegram.Bot(token=bot_token)
        self._admin_chat_id = admin_chat_id

    # ────────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────────

    async def send_alert(self, message: str, level: str = "INFO") -> None:
        """Send a notification to the admin chat.

        In development mode, also sends to all /start subscribers.

        Args:
            message: Body text of the notification.
            level: ``"INFO"``, ``"WARNING"``, or ``"ERROR"``.
        """
        emoji = _LEVEL_EMOJI.get(level.upper(), "ℹ️")
        now = datetime.now(_RIYADH_TZ).strftime("%Y-%m-%d %H:%M:%S")

        text = (
            f"{emoji} <b>{level.upper()}</b>\n"
            f"{message}\n"
            f"⏰ {now}"
        )

        # Collect all chat IDs to notify
        targets: list[int | str] = []
        if self._admin_chat_id:
            targets.append(self._admin_chat_id)

        # In dev mode, also send to /start subscribers
        try:
            from src.config import settings

            if settings.environment == "development":
                from src.telegram.admin_bot import get_subscriber_ids

                for sub_id in get_subscriber_ids():
                    if str(sub_id) != str(self._admin_chat_id):
                        targets.append(sub_id)
        except Exception:
            pass

        for chat_id in targets:
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
            except telegram.error.TelegramError as exc:
                logger.error("Failed to send admin alert to %s: %s", chat_id, exc)

    async def send_error(self, message: str) -> None:
        """Shortcut for ``send_alert(message, "ERROR")``."""
        await self.send_alert(message, "ERROR")

    async def send_startup_notification(self) -> None:
        """Announce that the bot has started."""
        await self.send_alert(
            "🟢 البوت يعمل الآن\nتم تشغيل نظام النشر التلقائي بنجاح.",
            "INFO",
        )

    async def send_shutdown_notification(self) -> None:
        """Announce that the bot is shutting down."""
        await self.send_alert("🔴 تم إيقاف البوت", "INFO")

    async def close(self) -> None:
        """Shut down the underlying HTTP session."""
        await self._bot.shutdown()
