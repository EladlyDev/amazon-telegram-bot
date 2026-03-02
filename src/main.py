"""Main entry point for the Amazon-Telegram bot.

Wires together all components — database, Amazon client, Telegram
publisher, admin bot, scheduler, and dashboard — and starts the
application with graceful shutdown handling.

Usage::

    python -m src.main
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from src.config import settings


# ── Logging ────────────────────────────────────────────────

def _configure_logging() -> None:
    """Set up console + rotating-file logging."""
    log_fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(log_fmt, datefmt=date_fmt))
    root.addHandler(console)

    # File handler (logs/bot.log, 10 MB, keep 5 backups)
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "bot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(log_fmt, datefmt=date_fmt))
    root.addHandler(file_handler)

    # Quiet noisy loggers
    for name in ("httpx", "httpcore", "apscheduler", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────

def _mask(value: str, visible: int = 4) -> str:
    """Show first *visible* chars of *value*, mask the rest."""
    if not value:
        return "(empty)"
    if len(value) <= visible:
        return value
    return value[:visible] + "***"


def _print_banner() -> None:
    print(
        "\n"
        "======================================\n"
        "  Amazon Telegram Bot v1.0\n"
        "  Starting up...\n"
        "======================================\n"
    )


def _log_config() -> None:
    """Log configuration summary (sensitive values masked)."""
    logger.info("Configuration:")
    logger.info("  Amazon access_key  = %s", _mask(settings.amazon_access_key))
    logger.info("  Amazon partner_tag = %s", _mask(settings.amazon_partner_tag))
    logger.info("  Telegram bot_token = %s", _mask(settings.telegram_bot_token))
    logger.info("  Telegram channel   = %s", _mask(settings.telegram_channel_id))
    logger.info("  Telegram admin     = %s", _mask(settings.telegram_admin_chat_id))
    logger.info("  Dashboard port     = %d", settings.dashboard_port)
    logger.info("  Database           = %s", settings.database_path)
    logger.info("  Environment        = %s", settings.environment)
    logger.info("  Log level          = %s", settings.log_level)


def _validate_config() -> None:
    """Emit warnings for missing configuration values."""
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN is empty — bot will not be able to publish.")
    if not settings.telegram_channel_id:
        logger.warning("TELEGRAM_CHANNEL_ID is empty — no target channel configured.")
    if not settings.amazon_access_key or not settings.amazon_secret_key:
        logger.info("Amazon PA API keys missing — will fall back to web scraper.")
    else:
        logger.info("Amazon PA API keys found — using PA API client.")


# ── Sync settings from .env → database ────────────────────

async def _sync_env_to_db(repo) -> None:
    """Push .env values into DB settings so they override any stale data."""
    pairs = [
        ("telegram.channel_id", settings.telegram_channel_id),
        ("telegram.admin_chat_id", settings.telegram_admin_chat_id),
        ("amazon.partner_tag", settings.amazon_partner_tag),
    ]
    for key, value in pairs:
        if value:
            await repo.set_setting(key, value)
    logger.debug("Synced .env overrides to database settings.")


# ── Main ───────────────────────────────────────────────────

async def main() -> None:  # noqa: C901 — orchestration function
    """Application entry point."""
    _configure_logging()
    _print_banner()
    _log_config()
    _validate_config()

    # ── 1. Database ────────────────────────────────────────
    from src.database.connection import init_db
    from src.database.repository import Repository

    await init_db()
    repo = Repository()
    logger.info("Database initialised.")

    # ── 2. Amazon client ───────────────────────────────────
    from src.amazon.factory import create_amazon_client

    source = await repo.get_setting("amazon.data_source") or "pa_api"
    amazon_client = create_amazon_client(
        source=source,
        access_key=settings.amazon_access_key,
        secret_key=settings.amazon_secret_key,
        partner_tag=settings.amazon_partner_tag,
    )

    # ── 3. Telegram services ──────────────────────────────
    from src.telegram.publisher import TelegramPublisher
    from src.telegram.notifier import AdminNotifier

    bot_token = settings.telegram_bot_token or "fake:token"
    publisher = TelegramPublisher(bot_token)
    notifier = AdminNotifier(bot_token, settings.telegram_admin_chat_id or "")

    # ── 4. Engine + scheduler ─────────────────────────────
    from src.engine.formatter import MessageFormatter
    from src.engine.core import BotEngine
    from src.engine.scheduler import PublishScheduler

    formatter = MessageFormatter()
    engine = BotEngine(amazon_client, publisher, notifier, repo, formatter)
    scheduler = PublishScheduler(engine, repo)
    await scheduler.start()
    logger.info("Scheduler started — jobs: %s", scheduler.get_scheduled_jobs_info())

    # ── 5. Admin bot ──────────────────────────────────────
    from src.telegram.admin_bot import AdminBot

    admin_bot = AdminBot(
        bot_token=bot_token,
        repo=repo,
        admin_chat_id=settings.telegram_admin_chat_id or "",
    )
    await admin_bot.start_polling()

    # ── 6. Sync .env → DB ────────────────────────────────
    await _sync_env_to_db(repo)

    # ── 7. Startup notification ───────────────────────────
    try:
        await notifier.send_startup_notification()
    except Exception as exc:
        logger.debug("Startup notification failed (non-fatal): %s", exc)

    # ── 8. Dashboard (blocks until shutdown) ──────────────
    from src.dashboard.app import create_dashboard_app

    app = create_dashboard_app(repo, scheduler, engine)
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.dashboard_port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    logger.info("Dashboard: http://0.0.0.0:%d", settings.dashboard_port)

    # Register signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def _handle_signal(sig: signal.Signals) -> None:
        logger.info("Received %s — shutting down...", sig.name)
        shutdown_event.set()
        server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    try:
        await server.serve()
    finally:
        # ── Graceful shutdown ─────────────────────────────
        logger.info("Shutting down services...")
        scheduler.stop()
        try:
            admin_bot.stop()
        except Exception:
            pass
        try:
            await notifier.send_shutdown_notification()
        except Exception:
            pass
        try:
            await publisher.close()
        except Exception:
            pass
        try:
            await notifier.close()
        except Exception:
            pass
        try:
            await amazon_client.close()
        except Exception:
            pass
        logger.info("Shutdown complete.")


# ── CLI entry ──────────────────────────────────────────────

def run() -> None:
    """Synchronous wrapper for :func:`main`."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run()
