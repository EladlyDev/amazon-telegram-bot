import asyncio
import logging
import uvicorn
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

async def main():
    from src.database.connection import init_db
    from src.database.repository import Repository
    from src.engine.formatter import MessageFormatter
    from src.engine.core import BotEngine
    from src.engine.scheduler import PublishScheduler
    from src.amazon.factory import create_amazon_client
    from src.telegram.publisher import TelegramPublisher
    from src.telegram.notifier import AdminNotifier
    from src.config import settings

    await init_db()
    repo = Repository()

    amazon_client = create_amazon_client(
        source="pa_api",
        access_key=settings.amazon_access_key or "",
        secret_key=settings.amazon_secret_key or "",
        partner_tag=settings.amazon_partner_tag or "",
    )
    publisher = TelegramPublisher(settings.telegram_bot_token or "fake:token")
    notifier = AdminNotifier(
        settings.telegram_bot_token or "fake:token",
        settings.telegram_admin_chat_id or "",
    )
    formatter = MessageFormatter()
    engine = BotEngine(amazon_client, publisher, notifier, repo, formatter)
    scheduler = PublishScheduler(engine, repo)
    await scheduler.start()

    # Start admin bot (template management via Telegram)
    from src.telegram.admin_bot import AdminBot
    admin_bot = AdminBot(
        bot_token=settings.telegram_bot_token or "fake:token",
        repo=repo,
        admin_chat_id=settings.telegram_admin_chat_id or "",
    )
    await admin_bot.start_polling()

    from src.dashboard.app import create_dashboard_app
    app = create_dashboard_app(repo, scheduler, engine)

    config = uvicorn.Config(app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    await server.serve()

asyncio.run(main())
