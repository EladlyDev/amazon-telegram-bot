"""Dev-mode /start handler for admin chat registration.

In development mode, anyone who sends /start to the bot gets their
chat_id saved so the AdminNotifier can send them notifications
(errors, info, startup/shutdown) — no need to manually copy chat IDs
into .env.

Subscribers are persisted in ``data/dev_subscribers.json``.
Only used when ``ENVIRONMENT=development``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SUBSCRIBERS_FILE = Path("data/dev_subscribers.json")


def _load_subscribers() -> set[int]:
    """Load subscriber chat IDs from disk."""
    if _SUBSCRIBERS_FILE.exists():
        try:
            return set(json.loads(_SUBSCRIBERS_FILE.read_text()))
        except (json.JSONDecodeError, TypeError):
            pass
    return set()


def _save_subscribers(subs: set[int]) -> None:
    """Persist subscriber chat IDs to disk."""
    _SUBSCRIBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SUBSCRIBERS_FILE.write_text(json.dumps(sorted(subs)))


def add_subscriber(chat_id: int) -> None:
    """Add a chat_id to the dev subscribers list."""
    subs = _load_subscribers()
    if chat_id not in subs:
        subs.add(chat_id)
        _save_subscribers(subs)
        logger.info("New dev admin subscriber: %d (total: %d)", chat_id, len(subs))


def get_subscriber_ids() -> list[int]:
    """Return all dev subscriber chat IDs."""
    return sorted(_load_subscribers())


class DevBot:
    """Simple bot that listens for /start and registers admin subscribers.

    Usage::

        bot = DevBot(token)
        await bot.start_polling()   # runs forever
    """

    def __init__(self, bot_token: str) -> None:
        from telegram.ext import ApplicationBuilder, CommandHandler

        self._app = (
            ApplicationBuilder()
            .token(bot_token)
            .build()
        )
        self._app.add_handler(CommandHandler("start", self._handle_start))

    async def _handle_start(self, update, context) -> None:  # noqa: ANN001
        """Handle /start — register the user as a dev admin."""
        chat_id = update.effective_chat.id
        name = update.effective_user.first_name or "مستخدم"
        add_subscriber(chat_id)
        await update.message.reply_text(
            f"مرحبا {name}! 👋\n"
            f"تم تسجيلك كمشرف تطوير.\n"
            f"ستصلك إشعارات البوت (أخطاء، تنبيهات، حالة التشغيل). 🔔",
        )

    async def start_polling(self) -> None:
        """Start listening for /start commands (blocking)."""
        logger.info("DevBot polling started — waiting for /start messages...")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

    async def stop(self) -> None:
        """Stop the polling loop."""
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
