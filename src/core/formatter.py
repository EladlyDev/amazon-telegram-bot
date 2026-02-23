"""Post formatter — renders Product data into Telegram-ready HTML messages."""

from __future__ import annotations

from typing import Any, Optional

import yaml
from loguru import logger

from src.amazon.product import Product


class PostFormatter:
    """Renders ``Product`` instances into formatted Telegram messages.

    Loads template definitions from a YAML file and applies them to
    product data, producing HTML text ready for ``sendMessage`` or
    ``sendPhoto`` captions.

    Args:
        template_path: Path to the post-template YAML file.
    """

    def __init__(self, template_path: str = "config/post_template.yaml") -> None:
        self._template_path = template_path
        self._templates: dict[str, Any] = {}
        self._active_template: str = "default"
        self._load(template_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def format_product(self, product: Product) -> dict:
        """Format a product into a Telegram-ready message dict.

        Args:
            product: The ``Product`` to format.

        Returns:
            A dict with keys ``text``, ``parse_mode``, and optionally
            ``image_url``.
        """
        try:
            tpl = self._templates.get(self._active_template)
            if tpl is None:
                tpl = next(iter(self._templates.values()))
                logger.warning(
                    "Active template '{}' not found, using fallback",
                    self._active_template,
                )

            # --- features -------------------------------------------
            features_formatted = self._build_features(product, tpl)

            # --- conditional lines -----------------------------------
            brand_line = ""
            if product.brand is not None and tpl.get("brand_line", ""):
                brand_line = tpl["brand_line"].replace("{brand}", self._escape_html(product.brand))

            prime_line = ""
            if product.is_prime and tpl.get("prime_line", ""):
                prime_line = tpl["prime_line"]

            # --- savings amount display ------------------------------
            savings_display = ""
            if product.savings_amount is not None:
                from src.amazon.product import _CURRENCY_SYMBOLS
                symbol = _CURRENCY_SYMBOLS.get(product.currency, product.currency)
                savings_display = f"{symbol}{product.savings_amount:,.2f}"

            # --- original price display ------------------------------
            if product.original_price is not None:
                from src.amazon.product import _CURRENCY_SYMBOLS
                symbol = _CURRENCY_SYMBOLS.get(product.currency, product.currency)
                original_display = f"{symbol}{product.original_price:,.2f}"
            else:
                original_display = "N/A"

            # --- body ------------------------------------------------
            body: str = tpl.get("body", "{title}")
            text = body.format(
                title=self._escape_html(product.title),
                brand_line=brand_line,
                original_price=original_display,
                current_price=product.price_display,
                savings_percent=product.discount_display,
                savings_amount=savings_display,
                prime_line=prime_line,
                features_formatted=features_formatted,
                url=product.url,  # inside <a href>, do NOT escape
                channel_footer=tpl.get("channel_footer", ""),
                asin=product.asin,
                category=product.category or "",
            )

            # --- clean up blank lines --------------------------------
            lines = text.split("\n")
            cleaned = [line for line in lines if line.strip()]
            text = "\n".join(cleaned)

            # --- result ----------------------------------------------
            result: dict[str, Any] = {
                "text": text,
                "parse_mode": "HTML",
            }

            if tpl.get("include_image", True) and product.image_url is not None:
                result["image_url"] = product.image_url

            return result

        except Exception:
            logger.exception("Failed to format product {}", product.asin)
            return {
                "text": f"<b>{self._escape_html(product.title)}</b>\n{product.url}",
                "parse_mode": "HTML",
            }

    def reload_templates(self, path: Optional[str] = None) -> None:
        """Re-read templates from disk.

        Args:
            path: Optional override path. Uses the original path if ``None``.
        """
        target = path or self._template_path
        self._load(target)
        logger.info("Templates reloaded from {}", target)

    def set_active_template(self, name: str) -> None:
        """Switch the active template by name.

        Args:
            name: Template name (must exist in the loaded templates).
        """
        if name in self._templates:
            self._active_template = name
            logger.info("Active template set to '{}'", name)
        else:
            logger.warning(
                "Template '{}' not found — available: {}",
                name,
                list(self._templates.keys()),
            )

    def get_available_templates(self) -> list[str]:
        """Return the names of all loaded templates."""
        return list(self._templates.keys())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load(self, path: str) -> None:
        """Parse the YAML template file."""
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            self._templates = data.get("templates", {})
            self._active_template = data.get("active_template", "default")
            logger.debug(
                "Loaded {} templates from {}", len(self._templates), path
            )
        except Exception:
            logger.exception("Failed to load templates from {}", path)
            self._templates = {}

    def _build_features(self, product: Product, tpl: dict) -> str:
        """Build the formatted features block."""
        max_features: int = tpl.get("features_max", 4)

        if max_features == 0:
            return ""

        features = product.features[:max_features]

        if not features:
            return "معلومات غير متوفرة"

        bullet: str = tpl.get("features_bullet", "- {feature}")
        lines: list[str] = []
        for feat in features:
            # Truncate overly long features
            if len(feat) > 100:
                feat = feat[:97] + "..."
            lines.append(
                bullet.replace("{feature}", self._escape_html(feat))
            )

        return "\n".join(lines)

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape characters that conflict with Telegram HTML parsing.

        Args:
            text: Raw text string.

        Returns:
            HTML-safe string.
        """
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
