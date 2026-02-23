"""Web scraper fallback for Amazon.sa product search.

Used when the PA API is unavailable or credentials aren't configured.
Parses search result pages using BeautifulSoup with the lxml backend.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from urllib.parse import quote_plus
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from src.amazon.base import AmazonClient
from src.amazon.models import Product

logger = logging.getLogger(__name__)

# Arabic-Indic numeral mapping (٠١٢٣٤٥٦٧٨٩ → 0123456789)
_ARABIC_NUMERAL_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


class AmazonScraper(AmazonClient):
    """Scrapes Amazon.sa search results into Product dataclasses."""

    BASE_URL = "https://www.amazon.sa"

    _USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv=126.0) Gecko/20100101 Firefox/126.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) Gecko/20100101 Firefox/126.0",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    ]

    def __init__(self, partner_tag: str = "") -> None:
        self._partner_tag = partner_tag
        self._client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
        )

    # ────────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────────

    async def search_products(
        self,
        keywords: str,
        search_index: str = "All",
        item_count: int = 10,
        min_price: int | None = None,
        max_price: int | None = None,
        browse_node: str | None = None,
    ) -> list[Product]:
        """Scrape Amazon.sa search results for the given keywords."""
        url = self._build_search_url(keywords, min_price, max_price, browse_node)
        logger.info("Scraping: %s", url)

        try:
            response = await self._client.get(url, headers=self._random_headers())
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.error("Scraper HTTP error %d for '%s'.", status, keywords)
            return []
        except httpx.RequestError as exc:
            logger.error("Scraper connection error: %s", exc)
            return []

        html = response.text

        # CAPTCHA detection
        if "captcha" in html.lower():
            logger.warning("CAPTCHA detected while scraping '%s'. Returning empty.", keywords)
            return []

        return self._parse_search_page(html, keywords, item_count)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ────────────────────────────────────────────────────────
    #  URL builder
    # ────────────────────────────────────────────────────────

    def _build_search_url(
        self,
        keywords: str,
        min_price: int | None,
        max_price: int | None,
        browse_node: str | None,
    ) -> str:
        """Construct the Amazon.sa search URL with optional filters."""
        url = f"{self.BASE_URL}/s?k={quote_plus(keywords)}"
        if min_price is not None and min_price > 0:
            url += f"&low-price={min_price}"
        if max_price is not None and max_price > 0:
            url += f"&high-price={max_price}"
        if browse_node:
            url += f"&rh=n:{browse_node}"
        return url

    # ────────────────────────────────────────────────────────
    #  Request helpers
    # ────────────────────────────────────────────────────────

    def _random_headers(self) -> dict[str, str]:
        """Return request headers with a random User-Agent."""
        return {
            "User-Agent": random.choice(self._USER_AGENTS),
            "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        }

    # ────────────────────────────────────────────────────────
    #  HTML parsing
    # ────────────────────────────────────────────────────────

    def _parse_search_page(
        self, html: str, keyword: str, item_count: int
    ) -> list[Product]:
        """Parse the full search results page into a list of Product."""
        soup = BeautifulSoup(html, "lxml")
        items = soup.find_all(attrs={"data-asin": True})

        products: list[Product] = []
        for item in items:
            if len(products) >= item_count:
                break

            asin = item.get("data-asin", "").strip()
            if not asin:
                continue

            try:
                product = self._parse_item(item, asin, keyword)
                if product:
                    products.append(product)
            except Exception as exc:
                logger.debug("Skipping item %s: %s", asin, exc)
                continue

        logger.info(
            "Scraper parsed %d products for '%s' from %d HTML elements.",
            len(products),
            keyword,
            len(items),
        )
        return products

    def _parse_item(self, el: Tag, asin: str, keyword: str) -> Product | None:
        """Parse a single search result element into a Product."""
        # ── Title ───────────────────────────────────────────
        title = self._extract_title(el)
        if not title:
            return None

        # ── Image ───────────────────────────────────────────
        image_url = self._extract_image(el)

        # ── Current price ───────────────────────────────────
        current_price = self._extract_current_price(el)
        if current_price <= 0:
            return None

        # ── Original price ──────────────────────────────────
        original_price = self._extract_original_price(el)
        if original_price <= 0:
            original_price = current_price

        # ── Prime ───────────────────────────────────────────
        is_prime = bool(
            el.select_one("[class*='a-icon-prime']")
        )

        # ── Rating ──────────────────────────────────────────
        rating = 0.0
        rating_el = el.find("span", class_="a-icon-alt")
        if rating_el:
            rating = self._parse_rating(rating_el.get_text(strip=True))

        # ── Reviews count ───────────────────────────────────
        reviews_count = 0
        # Try multiple selectors for review counts
        for sel in [
            "span.a-size-base.s-underline-text",
            "a[href*='#reviews'] span",
        ]:
            reviews_el = el.select_one(sel)
            if reviews_el:
                reviews_count = self._parse_int(reviews_el.get_text(strip=True))
                if reviews_count > 0:
                    break

        # ── Savings ─────────────────────────────────────────
        savings_amount = max(original_price - current_price, 0.0)
        savings_percent = 0.0
        if original_price > 0 and savings_amount > 0:
            savings_percent = round((savings_amount / original_price) * 100, 1)

        # ── Affiliate URL ──────────────────────────────────
        tag_part = f"&tag={self._partner_tag}" if self._partner_tag else ""
        affiliate_url = (
            f"{self.BASE_URL}/dp/{asin}?{tag_part}&linkCode=ogi&th=1&psc=1"
        )

        return Product(
            asin=asin,
            title=title,
            current_price=round(current_price, 2),
            original_price=round(original_price, 2),
            currency="SAR",
            savings_amount=round(savings_amount, 2),
            savings_percent=savings_percent,
            image_url=image_url,
            affiliate_url=affiliate_url,
            is_prime=is_prime,
            rating=rating,
            reviews_count=reviews_count,
            keyword_used=keyword,
        )

    # ────────────────────────────────────────────────────────
    #  Title & image extraction
    # ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_title(el: Tag) -> str:
        """Extract product title using multiple fallback selectors."""
        # Strategy 1: Amazon.sa uses span.a-truncate-full for the full title
        trunc = el.find("span", class_="a-truncate-full")
        if trunc:
            text = trunc.get_text(strip=True)
            if text:
                return text

        # Strategy 2: Classic layout with h2 > a > span.a-text-normal
        h2 = el.find("h2")
        if h2:
            span = h2.find("span", class_="a-text-normal")
            if span:
                return span.get_text(strip=True)
            link = h2.find("a")
            if link:
                return link.get_text(strip=True)

        # Strategy 3: Any span with class containing "a-text-normal"
        norm = el.find("span", class_="a-text-normal")
        if norm:
            return norm.get_text(strip=True)

        return ""

    @staticmethod
    def _extract_image(el: Tag) -> str:
        """Extract product image URL using multiple fallback selectors."""
        # Strategy 1: Classic s-image class
        img = el.find("img", class_="s-image")
        if img and img.get("src"):
            return img["src"]

        # Strategy 2: Amazon.sa custom class (matches _c2Itd_image_*)
        img = el.find("img", class_=lambda c: c and "image" in c.lower())
        if img and img.get("src"):
            return img["src"]

        # Strategy 3: Any img with Amazon CDN src
        for img in el.find_all("img"):
            src = img.get("src", "")
            if "media-amazon.com" in src:
                return src

        return ""

    # ────────────────────────────────────────────────────────
    #  Price extraction helpers
    # ────────────────────────────────────────────────────────

    def _extract_current_price(self, el: Tag) -> float:
        """Extract the current (discounted) price from a search result."""
        # Find span.a-price that is NOT .a-text-price (original)
        for price_tag in el.find_all("span", class_="a-price"):
            classes = price_tag.get("class", [])
            if "a-text-price" in classes:
                continue
            whole_el = price_tag.find("span", class_="a-price-whole")
            frac_el = price_tag.find("span", class_="a-price-fraction")
            if whole_el:
                whole = self._clean_price_text(whole_el.get_text(strip=True))
                fraction = "00"
                if frac_el:
                    fraction = self._clean_price_text(frac_el.get_text(strip=True))
                return self._safe_float(f"{whole}.{fraction}")
        return 0.0

    def _extract_original_price(self, el: Tag) -> float:
        """Extract the strikethrough (original) price from a search result."""
        original_tag = el.find("span", class_=lambda c: c and "a-text-price" in c.split())
        if not original_tag:
            return 0.0
        offscreen = original_tag.find("span", class_="a-offscreen")
        if offscreen:
            return self._parse_price_text(offscreen.get_text(strip=True))
        return 0.0

    # ────────────────────────────────────────────────────────
    #  Text / numeral helpers
    # ────────────────────────────────────────────────────────

    @staticmethod
    def _convert_arabic_numerals(text: str) -> str:
        """Convert Arabic-Indic numerals (٠-٩) to Western (0-9)."""
        return text.translate(_ARABIC_NUMERAL_MAP)

    def _clean_price_text(self, text: str) -> str:
        """Normalise price fragment: convert numerals, strip separators and Unicode marks."""
        text = self._convert_arabic_numerals(text)
        # Strip Unicode directional marks, NBSP, and other control chars
        text = re.sub(r"[\u200e\u200f\u200b\u200c\u200d\u00a0\ufeff]", "", text)
        text = text.replace(",", "").replace("٫", ".")
        # Keep only last dot as decimal separator
        if text.count(".") > 1:
            text = text.replace(".", "", text.count(".") - 1)
        # Remove trailing dot (e.g. "199.")
        text = text.rstrip(".")
        return text.strip()

    def _parse_price_text(self, text: str) -> float:
        """Parse a full price string like '٢٩٩٫٠٠ ر.س' into a float."""
        text = self._convert_arabic_numerals(text)
        # Strip Unicode control characters
        text = re.sub(r"[\u200e\u200f\u200b\u200c\u200d\u00a0\ufeff]", "", text)
        # Remove currency symbols and whitespace — keep digits, dots, commas
        text = re.sub(r"[^\d.,٫]", "", text)
        text = text.replace("٫", ".").replace(",", "")
        return self._safe_float(text)

    def _parse_rating(self, text: str) -> float:
        """Parse rating text like '4.5 من 5 نجوم' or '4.5 out of 5 stars'."""
        text = self._convert_arabic_numerals(text)
        match = re.search(r"(\d+\.?\d*)", text)
        return float(match.group(1)) if match else 0.0

    def _parse_int(self, text: str) -> int:
        """Parse an integer from text, handling Arabic numerals and commas."""
        text = self._convert_arabic_numerals(text)
        text = re.sub(r"[^\d]", "", text)
        return int(text) if text else 0

    @staticmethod
    def _safe_float(text: str) -> float:
        """Safely convert text to float, returning 0.0 on failure."""
        try:
            return float(text) if text else 0.0
        except ValueError:
            return 0.0
