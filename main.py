"""Bot orchestrator — coordinates fetching, filtering, and publishing."""

from __future__ import annotations

import asyncio
import os
import random
import signal
import sys
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from loguru import logger

from src.amazon.pa_api_client import (
    AmazonAPIConfig,
    AmazonAPIError,
    AmazonAPIThrottled,
    AmazonPAAPIClient,
)
from src.amazon.product import Product
from src.core.database import Database
from src.core.formatter import PostFormatter
from src.core.scheduler import BotScheduler
from src.telegram.publisher import TelegramPublisher
from src.utils.logger import setup_logger


class BotOrchestrator:
    """Central coordinator for the Amazon-to-Telegram pipeline.

    Loads all configuration, initialises every component, and drives
    the fetch → filter → publish cycle on a schedule.
    """

    def __init__(self) -> None:
        load_dotenv()

        # --- config --------------------------------------------------
        with open("config/settings.yaml", encoding="utf-8") as fh:
            self.settings: dict = yaml.safe_load(fh) or {}

        with open("config/categories.yaml", encoding="utf-8") as fh:
            self.categories: dict = yaml.safe_load(fh) or {}

        # --- logging -------------------------------------------------
        log_cfg = self.settings.get("logging", {})
        setup_logger(
            log_level=log_cfg.get("level", "INFO"),
            log_file=log_cfg.get("file", "logs/bot.log"),
            rotation=log_cfg.get("rotation", "10 MB"),
            retention=log_cfg.get("retention", "30 days"),
        )

        # --- components ----------------------------------------------
        self.db = Database("data/bot.db")

        amazon_cfg = self.settings.get("amazon", {})
        self.amazon_client = AmazonPAAPIClient(
            AmazonAPIConfig(
                access_key=os.getenv("AMAZON_ACCESS_KEY", ""),
                secret_key=os.getenv("AMAZON_SECRET_KEY", ""),
                partner_tag=os.getenv("AMAZON_PARTNER_TAG", ""),
                host=amazon_cfg.get("host", "webservices.amazon.com"),
                region=amazon_cfg.get("region", "us-east-1"),
                marketplace=amazon_cfg.get("marketplace", "www.amazon.com"),
            )
        )

        self.formatter = PostFormatter("config/post_template.yaml")

        tg_cfg = self.settings.get("telegram", {})
        self.publisher = TelegramPublisher(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            channel_id=tg_cfg.get("channel_id", ""),
            formatter=self.formatter,
            rate_limit_delay=float(tg_cfg.get("rate_limit_seconds", 3)),
        )

        self.scheduler = BotScheduler(self.settings)
        self.scheduler.set_callback(self.run_single_cycle)

        self._category_index: int = 0
        self._running: bool = True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Boot the bot: initialise DB, start scheduler, enter main loop."""
        # Ensure directories
        Path("data").mkdir(parents=True, exist_ok=True)
        Path("logs").mkdir(parents=True, exist_ok=True)

        await self.db.initialize()

        self.scheduler.setup_jobs()
        self.scheduler.start()

        logger.info("🚀 Bot started — scheduler running")

        # Admin notification
        admin_cfg = self.settings.get("admin", {})
        admin_chat = admin_cfg.get("chat_id", "")
        if admin_chat:
            await self.publisher.send_admin_notification(
                "Bot started successfully ✅", admin_chat
            )

        # Main keep-alive loop
        while self._running:
            await asyncio.sleep(1)

        await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully tear down all components."""
        logger.info("Shutting down...")
        self._running = False
        self.scheduler.stop()
        await self.amazon_client.close()
        await self.publisher.close()
        await self.db.close()
        logger.info("Shutdown complete ✅")

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    async def run_single_cycle(self) -> int:
        """Execute one fetch → filter → publish cycle.

        Returns:
            Number of products successfully published.
        """
        try:
            return await self._cycle_inner()
        except Exception:
            logger.exception("Unexpected error in publish cycle")
            return 0

    async def _cycle_inner(self) -> int:
        """Inner implementation of the publish cycle (unwrapped)."""
        # Step 1 — Check pause state
        paused = await self.db.get_config("paused", "false")
        if paused == "true":
            logger.info("Bot is paused — skipping cycle")
            return 0

        # Step 2 — Select category
        all_cats: list[dict] = self.categories.get("categories", [])
        enabled = [c for c in all_cats if c.get("enabled", True)]
        if not enabled:
            logger.warning("No enabled categories — skipping cycle")
            return 0

        category = enabled[self._category_index % len(enabled)]
        self._category_index += 1
        cat_name: str = category.get("name", "Unknown")

        # Step 3 — Select keyword
        keywords: list[str] = category.get("keywords", [])
        if not keywords:
            logger.warning("No keywords for category '{}' — skipping", cat_name)
            return 0
        keyword = random.choice(keywords)
        logger.info("Cycle start — category='{}', keyword='{}'", cat_name, keyword)

        # Step 4 — Fetch from Amazon
        admin_cfg = self.settings.get("admin", {})
        admin_chat: str = admin_cfg.get("chat_id", "")
        notify_errors: bool = admin_cfg.get("notify_errors", True)

        try:
            response = await self.amazon_client.search_items(
                keywords=keyword,
                search_index=category.get("search_index", "All"),
                item_count=10,
                min_saving_percent=category.get("min_saving_percent"),
            )
        except AmazonAPIThrottled:
            logger.warning("Rate limited by Amazon — will retry next cycle")
            if notify_errors and admin_chat:
                await self.publisher.send_admin_notification(
                    "⚠️ Amazon API rate limited — skipped cycle", admin_chat
                )
            return 0
        except AmazonAPIError as e:
            logger.error("Amazon API error: {}", e)
            if notify_errors and admin_chat:
                await self.publisher.send_admin_notification(
                    f"❌ Amazon API error: {e}", admin_chat
                )
            return 0
        except Exception as e:
            logger.error("Unexpected fetch error: {}", e)
            return 0

        # Step 5 — Parse products
        items: list[dict] = (
            response.get("SearchResult", {}).get("Items", [])
        )
        partner_tag = os.getenv("AMAZON_PARTNER_TAG", "")
        products: list[Product] = []
        for item in items:
            try:
                products.append(Product.from_api_response(item, partner_tag))
            except Exception:
                logger.debug("Skipped unparseable item")

        fetched_count = len(products)

        # Step 6 — Apply filters
        filters_cfg = self.settings.get("filters", {})
        min_price: float = filters_cfg.get("min_price", 0)
        max_price: float = filters_cfg.get("max_price", 999999)
        min_discount: int = filters_cfg.get("min_discount_percent", 0)
        must_image: bool = filters_cfg.get("must_have_image", False)
        prime_only: bool = filters_cfg.get("prime_only", False)
        cooldown: int = self.settings.get("dedup", {}).get("cooldown_days", 7)

        filtered: list[Product] = []
        for p in products:
            if p.current_price is None:
                continue
            if must_image and p.image_url is None:
                continue
            if p.current_price < min_price:
                continue
            if p.current_price > max_price:
                continue
            if prime_only and not p.is_prime:
                continue
            if p.has_discount and (p.savings_percent or 0) < min_discount:
                continue
            filtered.append(p)

        after_filters = len(filtered)

        # Dedup
        deduped: list[Product] = []
        for p in filtered:
            if not await self.db.is_recently_published(p.asin, cooldown):
                deduped.append(p)

        after_dedup = len(deduped)
        logger.info(
            "{} fetched → {} after filters → {} after dedup",
            fetched_count,
            after_filters,
            after_dedup,
        )

        # Step 7 — Publish
        products_per_run: int = self.settings.get("schedule", {}).get(
            "products_per_run", 3
        )
        to_publish = deduped[:products_per_run]
        channel_id: str = self.settings.get("telegram", {}).get("channel_id", "")
        published_count = 0

        for product in to_publish:
            product.category = cat_name
            msg_id = await self.publisher.publish_product(product)
            if msg_id is not None:
                await self.db.record_product(
                    product.asin,
                    product.title,
                    product.current_price,
                    product.original_price,
                    product.category,
                )
                await self.db.record_publication(
                    product.asin, channel_id, msg_id
                )
                published_count += 1

        logger.info("Cycle complete: {} products published", published_count)
        return published_count

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        """Register graceful shutdown on SIGINT / SIGTERM."""
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: self._handle_signal(s),
                )
            logger.debug("Signal handlers registered (asyncio)")
        except (NotImplementedError, RuntimeError):
            # Windows fallback
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda s, f: self._handle_signal(s))
            logger.debug("Signal handlers registered (signal module fallback)")

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle a termination signal."""
        logger.info("Signal {} received — initiating shutdown", sig.name)
        self._running = False


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


async def main() -> None:
    """Boot and run the bot orchestrator."""
    orchestrator = BotOrchestrator()
    orchestrator._setup_signal_handlers()
    await orchestrator.start()


if __name__ == "__main__":
    asyncio.run(main())
