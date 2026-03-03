"""Template rendering engine for Telegram product messages.

Replaces ``{variable}`` placeholders in a template string with
product data, formats numbers, escapes HTML, and cleans up
empty lines left by unused placeholders.
"""

from __future__ import annotations

import logging
import re
from html import escape as _html_escape

from src.amazon.models import Product

logger = logging.getLogger(__name__)


class MessageFormatter:
    """Renders a :class:`Product` into a Telegram message using a template."""

    # ────────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────────

    def render(
        self,
        template_text: str,
        product: Product,
        parse_mode: str = "HTML",
    ) -> str:
        """Replace ``{variable}`` placeholders with product data.

        Args:
            template_text: Template body with ``{var}`` placeholders.
            product: The product to render.
            parse_mode: ``"HTML"`` (default) — text fields are HTML-escaped.

        Returns:
            The fully-rendered message string, with empty gaps cleaned up.
        """
        is_html = parse_mode.upper() == "HTML"
        is_mdv2 = parse_mode.upper() in ("MARKDOWNV2", "MARKDOWN")
        esc = self._escape_html if is_html else (self._escape_mdv2 if is_mdv2 else _noop)

        has_discount = product.has_discount

        # ── Pre-process: strip lines that are irrelevant ────
        text = template_text
        if not has_discount:
            # Remove lines that have savings/discount placeholders
            # (but NOT the main price line which has {original_price} + {current_price})
            text = self._remove_lines_with(text, [
                "{savings_percent}", "{savings_amount}",
            ])

        variables: dict[str, str] = {
            "title": esc(product.title),
            "brand": esc(product.brand),
            "original_price": _fmt_price(product.original_price),
            "current_price": _fmt_price(product.current_price),
            "currency": product.currency,
            "savings_percent": str(int(product.savings_percent)) if has_discount else "",
            "savings_amount": _fmt_price(product.savings_amount) if has_discount else "",
            "features": product.features_formatted,
            "url": product.affiliate_url,  # NOT escaped — used inside link
            "prime_badge": product.prime_badge,
            "category": esc(product.category),
            "asin": product.asin,
            "rating": product.stars_display,
            "reviews_count": str(product.reviews_count) if product.reviews_count else "",
            "image_url": product.image_url,
            "deal_display": product.deal_display,
        }

        # Replace all {var} placeholders
        for key, value in variables.items():
            text = text.replace(f"{{{key}}}", value)

        # Post-process: collapse strikethrough price when no discount
        if not has_discount:
            if is_html:
                text = re.sub(
                    r"<s>[^<]*</s>\s*[➜→←⬅➨]\s*",
                    "",
                    text,
                )
            else:
                # MarkdownV2: ~price~ → price
                text = re.sub(
                    r"~[^~]*~\s*[➜→←⬅➨]\s*",
                    "",
                    text,
                )

        # Warn about any remaining unreplaced placeholders
        remaining = re.findall(r"\{(\w+)\}", text)
        if remaining:
            logger.warning(
                "Unresolved template variables: %s", ", ".join(remaining)
            )

        return self._clean_empty_lines(text)

    # ────────────────────────────────────────────────────────
    #  Helpers
    # ────────────────────────────────────────────────────────

    @staticmethod
    def _remove_lines_with(text: str, markers: list[str]) -> str:
        """Remove lines that contain any of the given marker strings."""
        lines = text.split("\n")
        result = []
        for line in lines:
            if any(m in line for m in markers):
                continue
            result.append(line)
        return "\n".join(result)

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape ``& < > "`` for safe inclusion in HTML messages."""
        return _html_escape(text, quote=True)

    @staticmethod
    def _escape_mdv2(text: str) -> str:
        """Escape MarkdownV2 special characters with backslashes."""
        special = r'_*[]()~`>#+-=|{}.!'
        return ''.join(f'\\{c}' if c in special else c for c in text)

    @staticmethod
    def _html_to_mdv2(text: str) -> str:
        """Convert HTML formatting tags to MarkdownV2 equivalents.

        Allows templates written with HTML tags to work seamlessly
        when the parse_mode is switched to MarkdownV2.
        """
        # Links: <a href="url">text</a> → [text](url)
        text = re.sub(
            r'<a\s+href=["\']([^"\']+)["\']>(.*?)</a>',
            r'[\2](\1)',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # Bold: <b>text</b> or <strong>text</strong> → *text*
        text = re.sub(r'<(?:b|strong)>(.*?)</(?:b|strong)>', r'*\1*', text, flags=re.IGNORECASE | re.DOTALL)
        # Italic: <i>text</i> or <em>text</em> → _text_
        text = re.sub(r'<(?:i|em)>(.*?)</(?:i|em)>', r'_\1_', text, flags=re.IGNORECASE | re.DOTALL)
        # Strikethrough: <s>text</s> or <del>text</del> → ~text~
        text = re.sub(r'<(?:s|del)>(.*?)</(?:s|del)>', r'~\1~', text, flags=re.IGNORECASE | re.DOTALL)
        # Code: <code>text</code> → `text`
        text = re.sub(r'<code>(.*?)</code>', r'`\1`', text, flags=re.IGNORECASE | re.DOTALL)
        # Underline: <u>text</u> → __text__
        text = re.sub(r'<u>(.*?)</u>', r'__\1__', text, flags=re.IGNORECASE | re.DOTALL)
        return text

    @staticmethod
    def _clean_empty_lines(text: str) -> str:
        """Remove empty/orphan lines and collapse 3+ newlines into 2.

        After variable substitution, some lines may only contain leftover
        punctuation, HTML tags, or emojis (e.g. ``💚 وفّر % ( ر.س)``).
        We strip those too.
        """
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped == "":
                cleaned.append("")  # keep as empty line (paragraph break)
                continue

            # Strip HTML tags    <b>, <s>, <a href='...'>, etc.
            no_html = re.sub(r"<[^>]+>", "", stripped)
            # Strip emojis and other symbol chars
            no_emoji = re.sub(
                r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u23F0\u2699\uFE0F]+",
                "",
                no_html,
            )
            # Strip common punctuation/whitespace left behind
            content_check = re.sub(r"[\s%()→➜←⬅➨:,.،\-—\u200f\u200e]+", "", no_emoji)

            if content_check:
                cleaned.append(line)
            # else: line is empty after removing tags/emoji/punctuation → skip

        text = "\n".join(cleaned)

        # Collapse 3+ consecutive newlines into 2 (one blank line)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Strip leading/trailing whitespace
        return text.strip()


def _fmt_price(value: float) -> str:
    """Format a price with commas and 2 decimal places."""
    if value == 0.0:
        return "0.00"
    return f"{value:,.2f}"


def _noop(text: str) -> str:
    """Identity function — no escaping."""
    return text
