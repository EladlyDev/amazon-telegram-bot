"""Admin bot with template management commands.

Provides Telegram-native template editing: admins can compose templates
directly in Telegram using its rich editor (emojis, bold, italic, etc.)
and the bot captures + converts them to HTML automatically.

Commands:
    /start          — register as admin subscriber
    /settemplate     — create or update a template
    /templates       — list all templates
    /activatetemplate — activate a template
    /deletetemplate  — delete a template
    /previewtemplate — preview with sample data
    /cancel          — cancel current operation
"""

from __future__ import annotations

import json
import logging
from html import escape as html_escape
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,

    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.database.repository import Repository

logger = logging.getLogger(__name__)

# ── Subscriber persistence (migrated from dev_bot.py) ────────

_SUBSCRIBERS_FILE = Path("data/dev_subscribers.json")


def _load_subscribers() -> set[int]:
    if _SUBSCRIBERS_FILE.exists():
        try:
            return set(json.loads(_SUBSCRIBERS_FILE.read_text()))
        except (json.JSONDecodeError, TypeError):
            pass
    return set()


def _save_subscribers(subs: set[int]) -> None:
    _SUBSCRIBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SUBSCRIBERS_FILE.write_text(json.dumps(sorted(subs)))


def add_subscriber(chat_id: int) -> None:
    subs = _load_subscribers()
    if chat_id not in subs:
        subs.add(chat_id)
        _save_subscribers(subs)
        logger.info("New admin subscriber: %d (total: %d)", chat_id, len(subs))


def get_subscriber_ids() -> list[int]:
    return sorted(_load_subscribers())


# ── Available template variables ─────────────────────────────

_VARIABLES = [
    ("{title}", "اسم المنتج"),
    ("{brand}", "الماركة"),
    ("{current_price}", "السعر الحالي"),
    ("{original_price}", "السعر الأصلي"),
    ("{savings_percent}", "نسبة الخصم"),
    ("{savings_amount}", "مبلغ التوفير"),
    ("{currency}", "العملة"),
    ("{url}", "رابط الشراء"),
    ("{rating}", "التقييم ⭐"),
    ("{reviews_count}", "عدد التقييمات"),
    ("{features}", "مميزات المنتج"),
    ("{prime_badge}", "شارة برايم"),
    ("{deal_display}", "عرض محدود"),
    ("{image_url}", "رابط الصورة"),
    ("{category}", "الفئة"),
    ("{asin}", "رمز المنتج"),
]





def _variable_list_text() -> str:
    """Format variable reference as readable text."""
    lines = ["<b>المتغيرات المتاحة:</b>\n"]
    for var_key, label in _VARIABLES:
        lines.append(f"<code>{var_key}</code> — {label}")
    return "\n".join(lines)


# ── Admin Bot ────────────────────────────────────────────────


class AdminBot:
    """Telegram bot with admin commands for template management.

    Usage::

        bot = AdminBot(token, repo, admin_chat_id)
        await bot.start_polling()
    """

    def __init__(
        self, bot_token: str, repo: Repository, admin_chat_id: str
    ) -> None:
        self._repo = repo
        self._admin_chat_id = admin_chat_id
        self._admin_ids: set[int] = set()
        if admin_chat_id:
            try:
                self._admin_ids.add(int(admin_chat_id))
            except ValueError:
                pass

        # Users currently in "waiting for template body" mode
        # Maps chat_id → template name
        self._waiting_template: dict[int, str] = {}

        # Build the application
        self._app = (
            ApplicationBuilder()
            .token(bot_token)
            .build()
        )

        # Register handlers (order matters for message handler)
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("settemplate", self._cmd_set_template))
        self._app.add_handler(CommandHandler("templates", self._cmd_list_templates))
        self._app.add_handler(
            CommandHandler("activatetemplate", self._cmd_activate_template)
        )
        self._app.add_handler(
            CommandHandler("deletetemplate", self._cmd_delete_template)
        )
        self._app.add_handler(
            CommandHandler("previewtemplate", self._cmd_preview_template)
        )
        self._app.add_handler(CommandHandler("cancel", self._cmd_cancel))

        # Catch-all message handler for template body capture
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

    # ── Authorization ────────────────────────────────────────

    def _is_admin(self, chat_id: int) -> bool:
        """Check if the user is an admin or dev subscriber."""
        if chat_id in self._admin_ids:
            return True
        return chat_id in _load_subscribers()

    # ── /start ───────────────────────────────────────────────

    async def _cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        name = update.effective_user.first_name or "مستخدم"
        add_subscriber(chat_id)
        self._admin_ids.add(chat_id)
        await update.message.reply_text(
            f"مرحبا {name}! 👋\n"
            f"تم تسجيلك كمشرف.\n"
            f"ستصلك إشعارات البوت وتقدر تدير القوالب من هنا. 🔔\n\n"
            f"الأوامر المتاحة:\n"
            f"/settemplate \"اسم\" — إنشاء/تعديل قالب\n"
            f"/templates — عرض القوالب\n"
            f"/activatetemplate \"اسم\" — تفعيل قالب\n"
            f"/deletetemplate \"اسم\" — حذف قالب\n"
            f"/previewtemplate \"اسم\" — معاينة قالب\n"
        )

    # ── /settemplate ─────────────────────────────────────────

    async def _cmd_set_template(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        if not self._is_admin(chat_id):
            return

        # Extract template name from command args
        name = " ".join(context.args).strip() if context.args else ""
        if not name:
            await update.message.reply_text(
                "⚠️ حدد اسم القالب:\n"
                "<code>/settemplate اسم القالب</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # Enter waiting mode
        self._waiting_template[chat_id] = name

        await update.message.reply_html(
            f"📝 <b>القالب: {name}</b>\n\n"
            f"أرسل رسالة القالب الآن...\n"
            f"استخدم التنسيق والإيموجي كما تحب، وأضف المتغيرات حيث تريد.\n\n"
            f"{_variable_list_text()}\n\n"
            f"❌ /cancel للإلغاء"
        )

    # ── Variable button tap ──────────────────────────────────



    # ── Message handler (template body capture) ──────────────

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        if chat_id not in self._waiting_template:
            return  # not in template mode

        name = self._waiting_template.pop(chat_id)

        # Convert Telegram entities to HTML automatically
        html_body = update.message.text_html

        # Save to database
        existing = await self._find_template_by_name(name)
        if existing:
            await self._repo.update_template(
                existing.id, {"body": html_body}
            )
            action = "تم تحديث"
        else:
            await self._repo.create_template({
                "name": name,
                "body": html_body,
                "parse_mode": "HTML",
                "include_image": True,
                "is_active": False,
            })
            action = "تم إنشاء"

        await update.message.reply_html(
            f"✅ {action} القالب '<b>{name}</b>'\n\n"
            f"لتفعيله:\n<code>/activatetemplate {name}</code>\n"
            f"للمعاينة:\n<code>/previewtemplate {name}</code>"
        )
        logger.info("Template '%s' saved via Telegram by user %d.", name, chat_id)

    # ── /templates ───────────────────────────────────────────

    async def _cmd_list_templates(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        if not self._is_admin(chat_id):
            return

        templates = await self._repo.get_all_templates()
        if not templates:
            await update.message.reply_text("📭 لا توجد قوالب محفوظة.")
            return

        lines = ["📋 <b>القوالب المحفوظة:</b>\n"]
        for t in templates:
            status = "✅" if t.is_active else "⬜"
            safe_name = html_escape(t.name)
            lines.append(f"{status} <b>{safe_name}</b> (#{t.id})")
            # Show first line of body as preview (escape HTML tags)
            preview = html_escape(t.body.split("\n")[0][:60])
            lines.append(f"    <i>{preview}...</i>")
        lines.append("\n💡 /activatetemplate \"اسم\" لتفعيل قالب")

        await update.message.reply_html("\n".join(lines))

    # ── /activatetemplate ────────────────────────────────────

    async def _cmd_activate_template(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        if not self._is_admin(chat_id):
            return

        name = " ".join(context.args).strip() if context.args else ""
        if not name:
            await update.message.reply_html(
                "⚠️ حدد اسم القالب:\n<code>/activatetemplate اسم القالب</code>"
            )
            return

        template = await self._find_template_by_name(name)
        if not template:
            await update.message.reply_text(f"❌ القالب '{name}' غير موجود.")
            return

        await self._repo.activate_template(template.id)
        await update.message.reply_html(
            f"✅ تم تفعيل القالب '<b>{name}</b>'\n"
            f"سيتم استخدامه في المنشورات القادمة."
        )
        logger.info("Template '%s' activated via Telegram.", name)

    # ── /deletetemplate ──────────────────────────────────────

    async def _cmd_delete_template(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        if not self._is_admin(chat_id):
            return

        name = " ".join(context.args).strip() if context.args else ""
        if not name:
            await update.message.reply_html(
                "⚠️ حدد اسم القالب:\n<code>/deletetemplate اسم القالب</code>"
            )
            return

        template = await self._find_template_by_name(name)
        if not template:
            await update.message.reply_text(f"❌ القالب '{name}' غير موجود.")
            return

        if template.is_active:
            await update.message.reply_text(
                "⚠️ لا يمكن حذف القالب المفعّل. فعّل قالباً آخر أولاً."
            )
            return

        await self._repo.delete_template(template.id)
        await update.message.reply_html(
            f"🗑 تم حذف القالب '<b>{name}</b>'"
        )
        logger.info("Template '%s' deleted via Telegram.", name)

    # ── /previewtemplate ─────────────────────────────────────

    async def _cmd_preview_template(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        if not self._is_admin(chat_id):
            return

        name = " ".join(context.args).strip() if context.args else ""
        if not name:
            await update.message.reply_html(
                "⚠️ حدد اسم القالب:\n<code>/previewtemplate اسم القالب</code>"
            )
            return

        template = await self._find_template_by_name(name)
        if not template:
            await update.message.reply_text(f"القالب '{name}' غير موجود.")
            return

        # Render with sample data
        from src.engine.formatter import MessageFormatter
        from src.amazon.models import Product

        sample = Product(
            asin="B0SAMPLE1",
            title="سماعات بلوتوث لاسلكية من أنكر",
            brand="أنكر",
            original_price=299.0,
            current_price=199.0,
            savings_percent=33,
            savings_amount=100.0,
            currency="SAR",
            is_prime=True,
            rating=4.7,
            reviews_count=1250,
            features=["صوت نقي عالي الجودة", "بطارية تدوم 40 ساعة", "شحن سريع USB-C"],
            affiliate_url="https://amazon.sa/dp/B0SAMPLE1",
            image_url="https://m.media-amazon.com/images/I/sample.jpg",
        )
        sample.category = "إلكترونيات"

        formatter = MessageFormatter()
        rendered = formatter.render(template.body, sample, template.parse_mode)

        await update.message.reply_text("👁 معاينة القالب:\n\n" + "─" * 30)
        await update.message.reply_html(rendered)

    # ── /cancel ──────────────────────────────────────────────

    async def _cmd_cancel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        if chat_id in self._waiting_template:
            name = self._waiting_template.pop(chat_id)
            await update.message.reply_text(
                f"❌ تم إلغاء إنشاء القالب '{name}'."
            )
        else:
            await update.message.reply_text("لا يوجد عملية جارية.")

    # ── Helpers ───────────────────────────────────────────────

    async def _find_template_by_name(self, name: str):
        """Find a template by name (case-insensitive)."""
        templates = await self._repo.get_all_templates()
        for t in templates:
            if t.name.strip().lower() == name.strip().lower():
                return t
        return None

    # ── Lifecycle ────────────────────────────────────────────

    async def start_polling(self) -> None:
        """Start listening for commands (non-blocking background task)."""
        import asyncio as _asyncio

        logger.info("AdminBot polling started -- listening for commands...")

        # Retry initialization (Telegram API can timeout after rapid restarts)
        for attempt in range(1, 4):
            try:
                await self._app.initialize()
                await self._app.start()
                break
            except Exception as exc:
                if attempt == 3:
                    logger.error("AdminBot failed to initialize after 3 attempts: %s", exc)
                    raise
                logger.warning(
                    "AdminBot init attempt %d/3 failed (%s), retrying in 5s...",
                    attempt, exc,
                )
                await _asyncio.sleep(5)

        # Register commands in Telegram's menu (non-fatal if it fails)
        try:
            from telegram import BotCommand
            await self._app.bot.set_my_commands([
                BotCommand("start", "تسجيل كمشرف"),
                BotCommand("settemplate", "إنشاء/تعديل قالب"),
                BotCommand("templates", "عرض القوالب"),
                BotCommand("activatetemplate", "تفعيل قالب"),
                BotCommand("deletetemplate", "حذف قالب"),
                BotCommand("previewtemplate", "معاينة قالب"),
                BotCommand("cancel", "إلغاء العملية الحالية"),
            ])
        except Exception as exc:
            logger.warning("Failed to set bot commands (non-fatal): %s", exc)

        # Clear stale connections from previous instances (non-fatal)
        try:
            await self._app.bot.delete_webhook(drop_pending_updates=True)
        except Exception as exc:
            logger.warning("Failed to delete webhook (non-fatal): %s", exc)

        await self._app.updater.start_polling(
            drop_pending_updates=True,
            poll_interval=2.0,  # slower polling = fewer 409s during restart
        )

    async def stop(self) -> None:
        """Stop the polling loop."""
        if self._app.updater.running:
            await self._app.updater.stop()
        if self._app.running:
            await self._app.stop()
        await self._app.shutdown()
