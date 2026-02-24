"""Dev bot entry point — run with: make dev-bot"""
import asyncio
import signal
import logging

logging.basicConfig(level=logging.INFO)

from src.telegram.dev_bot import DevBot
from src.config import settings


async def main():
    bot = DevBot(settings.telegram_bot_token)
    print("🤖 Dev bot running — send /start to your bot on Telegram!")
    print("   Press Ctrl+C to stop.\n")
    await bot.start_polling()

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    await stop.wait()

    await bot.stop()
    print("\n👋 Dev bot stopped.")


asyncio.run(main())
