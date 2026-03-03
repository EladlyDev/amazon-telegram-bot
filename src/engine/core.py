"""Main orchestration engine for the Amazon-Telegram publishing bot.

Each call to :meth:`run_publish_cycle` performs one complete cycle:
search → filter → deduplicate → format → publish → save.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from src.amazon.base import AmazonClient
from src.amazon.models import Product
from src.database.models import Category, Keyword
from src.database.repository import Repository
from src.engine.dedup import DuplicateChecker
from src.engine.formatter import MessageFormatter
from src.engine.rotation import CategoryRotator
from src.telegram.notifier import AdminNotifier
from src.telegram.publisher import TelegramPublishError, TelegramPublisher

logger = logging.getLogger(__name__)

# Delay between consecutive posts within a single batch (seconds)
_INTER_POST_DELAY = 5


class BotEngine:
    """Orchestrates the full search → publish pipeline."""

    def __init__(
        self,
        amazon_client: AmazonClient,
        publisher: TelegramPublisher,
        notifier: AdminNotifier,
        repository: Repository,
        formatter: MessageFormatter,
    ) -> None:
        self._amazon = amazon_client
        self._publisher = publisher
        self._notifier = notifier
        self._repo = repository
        self._formatter = formatter

        # Sub-components
        self.rotator = CategoryRotator(repository)
        self.dedup = DuplicateChecker(repository)

    # ────────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────────

    async def run_publish_cycle(self) -> None:
        """Execute one full publishing cycle.

        Reads ``products_per_batch`` from the active schedule and
        publishes that many products, with a short delay between each.
        """
        try:
            # 1. Check bot.is_running
            if not await self._repo.get_setting_bool("bot.is_running"):
                logger.debug("Bot is paused — skipping cycle.")
                return

            # 2. Get batch size from active schedule
            schedule = await self._repo.get_active_schedule()
            batch_size = schedule.products_per_batch if schedule else 1

            # 3. Publish batch
            success_count = 0
            for i in range(batch_size):
                ok = await self._publish_one()
                if ok:
                    success_count += 1
                # Delay between posts (skip after last one)
                if i < batch_size - 1:
                    await asyncio.sleep(_INTER_POST_DELAY)

            # 4. Log summary
            logger.info(
                "Cycle complete: %d/%d published.", success_count, batch_size
            )
            await self._repo.log_event(
                level="INFO",
                component="engine",
                action="cycle_complete",
                message=f"Cycle complete: {success_count}/{batch_size} published.",
            )

        except Exception as exc:
            logger.exception("Unhandled error in publish cycle: %s", exc)
            try:
                await self._notifier.send_error(
                    f"خطأ غير متوقع في دورة النشر:\n{exc}"
                )
            except Exception:
                pass
            await self._repo.log_event(
                level="ERROR",
                component="engine",
                action="cycle_error",
                message=str(exc),
            )

    async def trigger_manual(self) -> dict:
        """Trigger a single manual publish (from dashboard).

        Returns:
            ``{"success": bool, "message": str, "product_title": str | None}``
        """
        # Temporarily enable if paused
        was_running = await self._repo.get_setting_bool("bot.is_running")
        if not was_running:
            await self._repo.set_setting("bot.is_running", "true")

        try:
            ok = await self._publish_one()
            if ok:
                return {
                    "success": True,
                    "message": "تم النشر بنجاح",
                    "product_title": self._last_published_title,
                }
            return {
                "success": False,
                "message": "لم يتم العثور على منتج مناسب للنشر",
                "product_title": None,
            }
        finally:
            # Restore original state
            if not was_running:
                await self._repo.set_setting("bot.is_running", "false")

    # ────────────────────────────────────────────────────────
    #  Core pipeline
    # ────────────────────────────────────────────────────────

    _last_published_title: str | None = None

    async def _publish_one(self) -> bool:
        """Find and publish exactly one product. Returns ``True`` on success."""
        self._last_published_title = None

        # ── 1. ROTATION ─────────────────────────────────────
        category, keyword = await self.rotator.get_next()
        if category is None or keyword is None:
            logger.warning("No active categories/keywords — nothing to publish.")
            await self._repo.log_event(
                level="WARNING",
                component="engine",
                action="no_categories",
                message="No active categories or keywords available.",
            )
            return False

        logger.info(
            "Searching: [%s] keyword='%s'",
            category.name, keyword.keyword,
        )

        # ── 2. SEARCH ───────────────────────────────────────
        try:
            products = await self._amazon.search_products(
                keywords=keyword.keyword,
                search_index=category.amazon_search_index or "All",
                min_price=(
                    int(category.min_price * 100)
                    if category.min_price and category.min_price > 0
                    else None
                ),
                max_price=(
                    int(category.max_price * 100)
                    if category.max_price and category.max_price > 0
                    else None
                ),
                browse_node=category.amazon_browse_node or None,
            )
        except Exception as exc:
            logger.error("Amazon search failed for '%s': %s", keyword.keyword, exc)
            await self._repo.log_event(
                level="ERROR",
                component="engine",
                action="search_error",
                message=f"Search failed for '{keyword.keyword}': {exc}",
            )
            return False

        if not products:
            logger.info(
                "No results for '%s' — advancing rotation.", keyword.keyword
            )
            await self.rotator.advance()
            await self._repo.update_keyword_usage(keyword.id, products_found=0)
            await self._repo.log_event(
                level="WARNING",
                component="engine",
                action="no_results",
                message=(
                    f"'{keyword.keyword}' في {category.name_ar or category.name}: "
                    f"لم يتم العثور على أي منتج. جرّب كلمة مفتاحية مختلفة."
                ),
            )
            return False

        # ── 3. FILTER ───────────────────────────────────────
        filtered = self._apply_filters(products, category)
        logger.debug(
            "Filtered %d → %d products for [%s].",
            len(products), len(filtered), category.name,
        )

        if not filtered:
            # Build actionable filter summary
            filters = []
            if category.min_discount_percent and category.min_discount_percent > 0:
                filters.append(f"خصم ≥ {category.min_discount_percent}%")
            if category.min_price and category.min_price > 0:
                filters.append(f"سعر ≥ {category.min_price} ريال")
            if category.max_price and category.max_price > 0:
                filters.append(f"سعر ≤ {category.max_price} ريال")
            if category.require_prime:
                filters.append("برايم فقط")
            if category.require_image:
                filters.append("صورة مطلوبة")
            filter_hint = " | ".join(filters) if filters else "—"

            logger.info(
                "All %d products filtered out for '%s' — advancing.",
                len(products), keyword.keyword,
            )
            await self.rotator.advance()
            await self._repo.update_keyword_usage(
                keyword.id, products_found=len(products)
            )
            await self._repo.log_event(
                level="WARNING",
                component="engine",
                action="all_filtered",
                message=(
                    f"'{keyword.keyword}' في {category.name_ar or category.name}: "
                    f"تم العثور على {len(products)} منتج لكن لم يطابق أي منها شروط الفئة ({filter_hint}). "
                    f"عدّل إعدادات الفئة لتوسيع النتائج."
                ),
            )
            return False

        # ── 4. DEDUPLICATE ──────────────────────────────────
        unique = await self.dedup.filter_new(filtered)
        if not unique:
            logger.info(
                "All %d products are duplicates for '%s' — advancing.",
                len(filtered), keyword.keyword,
            )
            await self.rotator.advance()
            await self._repo.log_event(
                level="WARNING",
                component="engine",
                action="all_duplicates",
                message=(
                    f"'{keyword.keyword}': تم العثور على {len(products)} منتج، "
                    f"نجح {len(filtered)} منها من الفلاتر "
                    f"لكن جميعها نُشرت من قبل."
                ),
            )
            return False

        # ── 5. SELECT BEST (highest discount) ───────────────
        candidates = sorted(unique, key=lambda p: p.savings_percent, reverse=True)

        # ── 5b. VARIANT ENRICHMENT — try each candidate until one works ──
        from src.amazon.pa_api import PAAPIClient
        best = None
        original_asin = None
        for candidate in candidates:
            candidate.category = category.name_ar or category.name
            candidate.keyword_used = keyword.keyword
            original_asin = candidate.asin

            if isinstance(self._amazon, PAAPIClient):
                try:
                    candidate = await self._amazon._get_cheapest_variant(candidate)
                except Exception as exc:
                    logger.debug("Variant lookup failed for %s: %s", candidate.asin, exc)

                # If variant swap changed the ASIN, re-check dedup
                if candidate.asin != original_asin:
                    if await self.dedup.is_duplicate(candidate.asin):
                        logger.info(
                            "Variant ASIN %s is a duplicate — trying next candidate.",
                            candidate.asin,
                        )
                        continue

            best = candidate
            break

        if best is None:
            logger.info("All candidates' variants are duplicates — skipping.")
            await self.rotator.advance()
            return False

        # ── 5c. RATING ENRICHMENT (PA API doesn't return reviews for amazon.sa) ──
        if isinstance(self._amazon, PAAPIClient) and best.rating == 0:
            try:
                best = await self._amazon._enrich_rating(best)
            except Exception as exc:
                logger.debug("Rating enrichment failed for %s: %s", best.asin, exc)

        logger.info(
            "Selected: %s — %s (%.0f%% off)",
            best.asin, best.title[:50], best.savings_percent,
        )

        # ── 6. FORMAT ───────────────────────────────────────
        template = await self._repo.get_active_template()
        if template is None:
            logger.error("No active post template configured.")
            await self._repo.log_event(
                level="ERROR",
                component="engine",
                action="no_template",
                message="No active post template found.",
            )
            return False

        message = self._formatter.render(
            template.body, best, parse_mode=template.parse_mode
        )

        # ── 7. PUBLISH ──────────────────────────────────────
        channel_id = await self._repo.get_setting("telegram.channel_id")
        if not channel_id:
            # Fallback to config
            from src.config import settings
            channel_id = settings.telegram_channel_id

        image_url = best.image_url if template.include_image else None
        telegram_message_id: int | None = None

        try:
            telegram_message_id = await self._publisher.publish(
                channel_id=channel_id,
                text=message,
                image_url=image_url,
                parse_mode=template.parse_mode,
            )
        except TelegramPublishError as exc:
            logger.error("Telegram publish failed: %s", exc)
            await self._save_product(best, "failed", error_message=str(exc))
            await self._notifier.send_error(
                f"فشل نشر المنتج: {best.title[:60]}\n{exc}"
            )
            return False

        # ── 8. SAVE ─────────────────────────────────────────
        await self._save_product(
            best, "published", telegram_message_id=telegram_message_id
        )

        # Also mark the original search ASIN as published so it's
        # caught by dedup on future searches (variant swap changes ASIN).
        if original_asin != best.asin:
            await self._save_product(
                best, "published",
                telegram_message_id=telegram_message_id,
                override_asin=original_asin,
            )

        # ── 9. UPDATE STATS ─────────────────────────────────
        await self._repo.update_keyword_usage(
            keyword.id, products_found=len(filtered)
        )
        await self.rotator.advance()

        # Increment total published counter
        total = await self._repo.get_setting_int("bot.total_published")
        await self._repo.set_setting("bot.total_published", str(total + 1))
        await self._repo.set_setting(
            "bot.last_publish_at",
            datetime.now(timezone.utc).isoformat(),
        )

        # ── 10. LOG ─────────────────────────────────────────
        self._last_published_title = best.title
        logger.info(
            "Published: [%s] %s (msg_id=%s)",
            best.asin, best.title[:50], telegram_message_id,
        )
        await self._repo.log_event(
            level="INFO",
            component="engine",
            action="published",
            message=f"Published {best.asin}: {best.title[:80]}",
            details=f"price={best.current_price} discount={best.savings_percent}%",
        )
        return True

    # ────────────────────────────────────────────────────────
    #  Helpers
    # ────────────────────────────────────────────────────────

    @staticmethod
    def _apply_filters(
        products: list[Product], category: Category
    ) -> list[Product]:
        """Apply category-level criteria to a list of products."""
        result: list[Product] = []

        for p in products:
            # Minimum discount
            if (
                category.min_discount_percent
                and category.min_discount_percent > 0
                and p.savings_percent < category.min_discount_percent
            ):
                continue

            # Price range (SAR)
            if category.min_price and category.min_price > 0:
                if p.current_price < category.min_price:
                    continue
            if category.max_price and category.max_price > 0:
                if p.current_price > category.max_price:
                    continue

            # Prime requirement
            if category.require_prime and not p.is_prime:
                continue

            # Image requirement
            if category.require_image and not p.image_url:
                continue

            result.append(p)

        return result

    async def _save_product(
        self,
        product: Product,
        status: str,
        *,
        telegram_message_id: int | None = None,
        error_message: str | None = None,
        override_asin: str | None = None,
    ) -> None:
        """Persist a product record to the database."""
        try:
            data = {
                "asin": override_asin or product.asin,
                "title": product.title,
                "brand": product.brand,
                "original_price": product.original_price,
                "current_price": product.current_price,
                "currency": product.currency,
                "savings_percent": product.savings_percent,
                "savings_amount": product.savings_amount,
                "features": "\n".join(product.features),
                "image_url": product.image_url,
                "affiliate_url": product.affiliate_url,
                "category_name": product.category,
                "keyword_used": product.keyword_used,
                "is_prime": product.is_prime,
                "rating": product.rating,
                "reviews_count": product.reviews_count,
                "status": status,
                "telegram_message_id": telegram_message_id,
                "error_message": error_message,
            }
            await self._repo.save_published_product(data)
        except Exception as exc:
            logger.error("Failed to save published product: %s", exc)
