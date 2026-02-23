"""Telegram channel publisher — raw HTTP Bot API via httpx."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx
from loguru import logger

from src.amazon.product import Product
from src.core.formatter import PostFormatter


class TelegramPublisher:
    """Publishes formatted product posts to a Telegram channel.

    Uses ``httpx.AsyncClient`` to call the Telegram Bot API directly
    (no ``python-telegram-bot`` dependency). Handles rate limiting,
    retries, and caption length limits.

    Args:
        bot_token: Telegram bot token from BotFather.
        channel_id: Target channel (e.g. ``"@my_channel"``).
        formatter: A ``PostFormatter`` instance for rendering products.
        rate_limit_delay: Minimum seconds between messages.
    """

    BASE_URL = "https://api.telegram.org/bot{token}"

    def __init__(
        self,
        bot_token: str,
        channel_id: str,
        formatter: PostFormatter,
        rate_limit_delay: float = 3.0,
    ) -> None:
        self._token = bot_token
        self._channel_id = channel_id
        self.formatter = formatter
        self._rate_limit_delay = rate_limit_delay
        self._base_url = self.BASE_URL.format(token=bot_token)
        self._client = httpx.AsyncClient(timeout=30.0)
        self._last_sent: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def publish_product(self, product: Product) -> Optional[int]:
        """Format and publish a single product to the channel.

        Args:
            product: The ``Product`` to publish.

        Returns:
            The Telegram ``message_id`` on success, or ``None`` on failure.
        """
        try:
            await self._respect_rate_limit()

            formatted = self.formatter.format_product(product)
            text: str = formatted["text"]
            parse_mode: str = formatted.get("parse_mode", "HTML")
            image_url: Optional[str] = formatted.get("image_url")

            if image_url:
                # Photo caption limit: 1024 chars
                if len(text) > 1024:
                    text = text[:1020] + "..."
                data = await self._send_photo(image_url, text, parse_mode)
            else:
                # Message limit: 4096 chars
                if len(text) > 4096:
                    text = text[:4090] + "..."
                data = await self._send_message(text, parse_mode)

            if data.get("ok"):
                message_id = data["result"]["message_id"]
                logger.info(
                    "Published {} to {} (msg_id={})",
                    product.asin,
                    self._channel_id,
                    message_id,
                )
                return message_id

            logger.error(
                "Failed to publish {}: {}",
                product.asin,
                data.get("description", "unknown error"),
            )
            return None

        except Exception:
            logger.exception("Unexpected error publishing {}", product.asin)
            return None

    async def publish_batch(self, products: list[Product]) -> list[int]:
        """Publish a list of products sequentially.

        Args:
            products: Products to publish.

        Returns:
            List of successful ``message_id`` values.
        """
        message_ids: list[int] = []
        total = len(products)

        for idx, product in enumerate(products, 1):
            logger.info(
                "Publishing {}/{}: {}", idx, total, product.asin
            )
            msg_id = await self.publish_product(product)
            if msg_id is not None:
                message_ids.append(msg_id)

        logger.info(
            "Batch complete: {}/{} published successfully",
            len(message_ids),
            total,
        )
        return message_ids

    async def send_admin_notification(
        self, text: str, admin_chat_id: str
    ) -> None:
        """Send a plain-text notification to the admin.

        Args:
            text: Notification body.
            admin_chat_id: Telegram chat ID of the admin.
        """
        try:
            payload = {
                "chat_id": admin_chat_id,
                "text": f"🤖 Bot Notification:\n\n{text}",
            }
            url = f"{self._base_url}/sendMessage"
            await self._api_call(url, payload, retries=1)
            logger.debug("Admin notification sent to {}", admin_chat_id)
        except Exception:
            logger.exception("Failed to send admin notification")

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        try:
            await self._client.aclose()
            logger.debug("Telegram publisher client closed")
        except Exception:
            logger.exception("Error closing Telegram publisher client")

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    async def _send_message(
        self, text: str, parse_mode: str = "HTML"
    ) -> dict:
        """Send a text message to the channel.

        Args:
            text: Message text.
            parse_mode: Telegram parse mode.

        Returns:
            Parsed API response dict.
        """
        payload = {
            "chat_id": self._channel_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False,
        }
        url = f"{self._base_url}/sendMessage"
        return await self._api_call(url, payload)

    async def _send_photo(
        self,
        photo_url: str,
        caption: str,
        parse_mode: str = "HTML",
    ) -> dict:
        """Send a photo with caption to the channel.

        Args:
            photo_url: URL of the image.
            caption: Photo caption text.
            parse_mode: Telegram parse mode.

        Returns:
            Parsed API response dict.
        """
        payload = {
            "chat_id": self._channel_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": parse_mode,
        }
        url = f"{self._base_url}/sendPhoto"
        return await self._api_call(url, payload)

    async def _api_call(
        self, url: str, payload: dict, retries: int = 3
    ) -> dict:
        """Make a POST request to the Telegram Bot API with retries.

        Handles HTTP 429 (rate-limit) with ``retry_after``, and retries
        other transient errors with exponential back-off.

        Args:
            url: Full API endpoint URL.
            payload: JSON body.
            retries: Maximum number of attempts.

        Returns:
            Parsed response dict (always has an ``"ok"`` key).
        """
        for attempt in range(retries):
            try:
                response = await self._client.post(url, json=payload)
                data = response.json()

                if data.get("ok"):
                    return data

                # Telegram rate limit
                if response.status_code == 429:
                    retry_after = (
                        data.get("parameters", {}).get("retry_after", 5)
                    )
                    logger.warning(
                        "Telegram rate-limited, retry after {}s", retry_after
                    )
                    await asyncio.sleep(retry_after)
                    continue

                # Other API error
                description = data.get("description", "unknown")
                logger.error(
                    "Telegram API error (attempt {}/{}): {} — {}",
                    attempt + 1,
                    retries,
                    response.status_code,
                    description,
                )
                await asyncio.sleep(2 ** attempt)

            except httpx.HTTPError as exc:
                logger.error(
                    "HTTP error (attempt {}/{}): {}",
                    attempt + 1,
                    retries,
                    exc,
                )
                await asyncio.sleep(2 ** attempt)

        return {"ok": False, "description": "retries exhausted"}

    async def _respect_rate_limit(self) -> None:
        """Sleep if needed to honour the configured rate limit."""
        now = time.monotonic()
        elapsed = now - self._last_sent
        if elapsed < self._rate_limit_delay:
            wait = self._rate_limit_delay - elapsed
            logger.debug("Rate limit: sleeping {:.1f}s", wait)
            await asyncio.sleep(wait)
        self._last_sent = time.monotonic()
