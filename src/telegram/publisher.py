"""Telegram channel publisher with retry logic, image support, and caption handling.

Uses python-telegram-bot v21 async API (Bot) directly — no Application needed.
"""

from __future__ import annotations

import asyncio
import logging
import re

import telegram
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# Telegram limits
_CAPTION_LIMIT = 1024
_MESSAGE_LIMIT = 4096
_MAX_RETRIES = 3


class TelegramPublishError(Exception):
    """Raised when publishing to Telegram fails after all retries."""

    pass


class TelegramPublisher:
    """Publishes formatted product messages to a Telegram channel."""

    def __init__(self, bot_token: str) -> None:
        self._bot = telegram.Bot(token=bot_token)

    # ────────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────────

    async def publish(
        self,
        channel_id: str,
        text: str,
        image_url: str | None = None,
        parse_mode: str = "HTML",
    ) -> int:
        """Publish a message (optionally with an image) to the channel.

        Args:
            channel_id: Telegram channel ID (e.g. ``"@my_channel"`` or ``"-100xxx"``).
            text: Message body (HTML or Markdown).
            image_url: Optional product image URL.
            parse_mode: ``"HTML"`` or ``"Markdown"``.

        Returns:
            The ``message_id`` of the sent Telegram message.

        Raises:
            TelegramPublishError: After all retries are exhausted.
        """
        pm = self._resolve_parse_mode(parse_mode)

        if image_url:
            return await self._send_photo_with_fallback(
                channel_id, text, image_url, pm
            )
        return await self._send_text(channel_id, text, pm)

    async def delete_message(self, channel_id: str, message_id: int) -> bool:
        """Delete a previously sent message. Returns ``True`` on success."""
        try:
            await self._bot.delete_message(chat_id=channel_id, message_id=message_id)
            return True
        except telegram.error.TelegramError as exc:
            logger.warning("Failed to delete message %d: %s", message_id, exc)
            return False

    async def close(self) -> None:
        """Shut down the underlying HTTP session."""
        await self._bot.shutdown()

    # ────────────────────────────────────────────────────────
    #  Photo + fallback
    # ────────────────────────────────────────────────────────

    async def _send_photo_with_fallback(
        self,
        channel_id: str,
        text: str,
        image_url: str,
        pm: ParseMode,
    ) -> int:
        """Try to send a photo with caption; fall back to text-only on failure."""
        caption = self._truncate_caption(text)

        try:
            msg = await self._retry(
                self._bot.send_photo,
                chat_id=channel_id,
                photo=image_url,
                caption=caption,
                parse_mode=pm,
            )
            # If caption was truncated, send the rest as a follow-up
            if len(text) > _CAPTION_LIMIT:
                remaining = text[len(caption):]
                if remaining.strip():
                    await self._retry(
                        self._bot.send_message,
                        chat_id=channel_id,
                        text=remaining.strip(),
                        parse_mode=pm,
                        disable_web_page_preview=True,
                    )
            return msg.message_id

        except (telegram.error.BadRequest, telegram.error.TelegramError) as exc:
            logger.warning(
                "send_photo failed (%s), falling back to text-only.", exc
            )
            return await self._send_text(
                channel_id, text, pm, disable_preview=False
            )

    # ────────────────────────────────────────────────────────
    #  Text-only send
    # ────────────────────────────────────────────────────────

    async def _send_text(
        self,
        channel_id: str,
        text: str,
        pm: ParseMode,
        disable_preview: bool = False,
    ) -> int:
        """Send a text-only message (with optional link preview)."""
        truncated = text[:_MESSAGE_LIMIT] if len(text) > _MESSAGE_LIMIT else text
        msg = await self._retry(
            self._bot.send_message,
            chat_id=channel_id,
            text=truncated,
            parse_mode=pm,
            disable_web_page_preview=disable_preview,
        )
        return msg.message_id

    # ────────────────────────────────────────────────────────
    #  Retry logic
    # ────────────────────────────────────────────────────────

    @staticmethod
    async def _retry(func, **kwargs):  # noqa: ANN001, ANN202
        """Execute *func* with exponential-backoff retry on transient errors."""
        last_exc: Exception | None = None
        backoff = 5  # seconds

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return await func(**kwargs)

            except telegram.error.RetryAfter as exc:
                wait = exc.retry_after
                logger.warning(
                    "Rate-limited (attempt %d/%d). Waiting %ds.",
                    attempt, _MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                last_exc = exc

            except telegram.error.TimedOut as exc:
                logger.warning(
                    "Timed out (attempt %d/%d). Waiting 5s.",
                    attempt, _MAX_RETRIES,
                )
                await asyncio.sleep(5)
                last_exc = exc

            except telegram.error.BadRequest as exc:
                # Non-retryable — likely bad HTML or invalid media
                logger.error("BadRequest (no retry): %s", exc)
                raise TelegramPublishError(str(exc)) from exc

            except telegram.error.TelegramError as exc:
                logger.warning(
                    "TelegramError (attempt %d/%d): %s. Backoff %ds.",
                    attempt, _MAX_RETRIES, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= 2
                last_exc = exc

        raise TelegramPublishError(
            f"Failed after {_MAX_RETRIES} retries: {last_exc}"
        ) from last_exc

    # ────────────────────────────────────────────────────────
    #  Helpers
    # ────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_parse_mode(mode: str) -> ParseMode:
        """Map a string parse-mode to the ``telegram.constants`` enum."""
        upper = mode.upper()
        if upper == "MARKDOWN" or upper == "MARKDOWNV2":
            return ParseMode.MARKDOWN_V2
        return ParseMode.HTML

    @staticmethod
    def _truncate_caption(text: str) -> str:
        """Truncate *text* to fit within the Telegram caption limit.

        Cuts at the last complete line before the limit and ensures
        no HTML tags are left open.
        """
        if len(text) <= _CAPTION_LIMIT:
            return text

        # Cut at the last newline before the limit
        cut = text[: _CAPTION_LIMIT - 3]
        last_nl = cut.rfind("\n")
        if last_nl > _CAPTION_LIMIT // 2:
            cut = cut[:last_nl]

        # Close any open HTML tags
        open_tags: list[str] = re.findall(r"<(\w+)(?:\s[^>]*)?>", cut)
        close_tags: list[str] = re.findall(r"</(\w+)>", cut)
        for tag in reversed(open_tags):
            if tag not in close_tags:
                cut += f"</{tag}>"
            else:
                close_tags.remove(tag)

        return cut
