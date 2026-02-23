"""Amazon Product Advertising API 5.0 client for amazon.sa.

Implements full AWS Signature V4 request signing from scratch (no boto3)
and response parsing into Product dataclasses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.amazon.base import AmazonClient
from src.amazon.models import Product

logger = logging.getLogger(__name__)


class PAAPIClient(AmazonClient):
    """PA API 5.0 client targeting Amazon Saudi Arabia."""

    # ── PA API constants ────────────────────────────────────
    HOST = "webservices.amazon.sa"
    REGION = "eu-west-1"
    SERVICE = "ProductAdvertisingAPI"
    SEARCH_PATH = "/paapi5/searchitems"
    SEARCH_TARGET = (
        "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems"
    )

    # Resources to request from the API (OffersV2 replaces deprecated Offers)
    _RESOURCES = [
        "Images.Primary.Large",
        "ItemInfo.Title",
        "ItemInfo.Features",
        "ItemInfo.ByLineInfo",
        "OffersV2.Listings.Price",
        "OffersV2.Listings.MerchantInfo",
        "OffersV2.Listings.Availability",
        "OffersV2.Listings.Condition",
        "OffersV2.Listings.DealDetails",
        "Offers.Listings.DeliveryInfo.IsPrimeEligible",
    ]

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        partner_tag: str,
    ) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._partner_tag = partner_tag
        self._client = httpx.AsyncClient(timeout=15.0)

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
        """Search Amazon.sa via PA API and return parsed products."""
        payload = self._build_payload(
            keywords, search_index, item_count, min_price, max_price, browse_node
        )
        body = json.dumps(payload, separators=(",", ":"))
        headers = self._sign_request(body)

        try:
            response = await self._client.post(
                f"https://{self.HOST}{self.SEARCH_PATH}",
                content=body,
                headers=headers,
            )
            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                logger.warning("PA API rate limited (429). Returning empty list.")
            else:
                logger.error(
                    "PA API HTTP error %s: %s",
                    exc.response.status_code,
                    exc.response.text[:500],
                )
            return []

        except httpx.RequestError as exc:
            logger.error("PA API connection error: %s", exc)
            return []

        try:
            data = response.json()
        except Exception:
            logger.error("PA API returned non-JSON response.")
            return []

        return self._parse_response(data, keywords)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ────────────────────────────────────────────────────────
    #  Payload builder
    # ────────────────────────────────────────────────────────

    def _build_payload(
        self,
        keywords: str,
        search_index: str,
        item_count: int,
        min_price: int | None,
        max_price: int | None,
        browse_node: str | None,
    ) -> dict[str, Any]:
        """Construct the PA API SearchItems request body."""
        payload: dict[str, Any] = {
            "Keywords": keywords,
            "SearchIndex": search_index,
            "ItemCount": min(item_count, 10),
            "PartnerTag": self._partner_tag,
            "PartnerType": "Associates",
            "Marketplace": "www.amazon.sa",
            "Resources": self._RESOURCES,
        }
        if min_price is not None and min_price > 0:
            payload["MinPrice"] = min_price * 100  # SAR → halalas
        if max_price is not None and max_price > 0:
            payload["MaxPrice"] = max_price * 100
        if browse_node:
            payload["BrowseNodeId"] = browse_node
        return payload

    # ────────────────────────────────────────────────────────
    #  AWS Signature V4 (from scratch)
    # ────────────────────────────────────────────────────────

    def _sign_request(self, body: str) -> dict[str, str]:
        """Sign the request using AWS Signature V4 and return headers."""
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        # ── Headers to sign (sorted by name) ────────────────
        headers = {
            "content-encoding": "amz-1.0",
            "content-type": "application/json; charset=utf-8",
            "host": self.HOST,
            "x-amz-date": amz_date,
            "x-amz-target": self.SEARCH_TARGET,
        }
        signed_headers = ";".join(sorted(headers.keys()))

        # ── Step 1: Canonical request ───────────────────────
        canonical_headers = "".join(
            f"{k}:{v}\n" for k, v in sorted(headers.items())
        )
        payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

        canonical_request = "\n".join([
            "POST",
            self.SEARCH_PATH,
            "",                    # empty query string
            canonical_headers,
            signed_headers,
            payload_hash,
        ])

        # ── Step 2: String to sign ──────────────────────────
        credential_scope = f"{date_stamp}/{self.REGION}/{self.SERVICE}/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])

        # ── Step 3: Signing key ─────────────────────────────
        signing_key = self._derive_signing_key(date_stamp)
        signature = hmac.new(
            signing_key,
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # ── Step 4: Authorization header ────────────────────
        authorization = (
            f"AWS4-HMAC-SHA256 "
            f"Credential={self._access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

        headers["authorization"] = authorization
        return headers

    def _derive_signing_key(self, date_stamp: str) -> bytes:
        """Derive the AWS4 signing key via HMAC-SHA256 chain."""

        def _hmac(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = _hmac(f"AWS4{self._secret_key}".encode("utf-8"), date_stamp)
        k_region = _hmac(k_date, self.REGION)
        k_service = _hmac(k_region, self.SERVICE)
        k_signing = _hmac(k_service, "aws4_request")
        return k_signing

    # ────────────────────────────────────────────────────────
    #  Response parsing
    # ────────────────────────────────────────────────────────

    def _parse_response(
        self, data: dict[str, Any], keyword: str
    ) -> list[Product]:
        """Extract Product objects from the PA API JSON response."""
        items = data.get("SearchResult", {}).get("Items", [])
        if not items:
            errors = data.get("Errors", [])
            if errors:
                logger.warning(
                    "PA API returned errors: %s",
                    [e.get("Message", "") for e in errors],
                )
            return []

        products: list[Product] = []
        for item in items:
            try:
                product = self._parse_item(item, keyword)
                products.append(product)
            except (KeyError, TypeError, IndexError) as exc:
                asin = item.get("ASIN", "unknown")
                logger.debug("Skipping item %s: %s", asin, exc)
                continue

        logger.info(
            "PA API returned %d items, parsed %d products for '%s'.",
            len(items),
            len(products),
            keyword,
        )
        return products

    @staticmethod
    def _parse_item(item: dict[str, Any], keyword: str) -> Product:
        """Parse a single PA API item dict into a Product."""
        asin = item["ASIN"]
        detail_url = item.get("DetailPageURL", "")

        # ── Item info ───────────────────────────────────────
        info = item.get("ItemInfo", {})
        title = info.get("Title", {}).get("DisplayValue", "")
        brand = (
            info.get("ByLineInfo", {}).get("Brand", {}).get("DisplayValue", "")
        )
        raw_features = (
            info.get("Features", {}).get("DisplayValues", [])
        )
        features = raw_features[:5]

        # ── Image ───────────────────────────────────────────
        image_url = (
            item.get("Images", {})
            .get("Primary", {})
            .get("Large", {})
            .get("URL", "")
        )

        # ── Pricing (OffersV2 first, fallback to legacy Offers) ──
        current_price = 0.0
        original_price = 0.0
        currency = "SAR"
        savings_amount = 0.0
        savings_percent = 0.0
        is_prime = False

        # Try OffersV2 (current API)
        offers_v2 = item.get("OffersV2", {})
        v2_listings = offers_v2.get("Listings", [])
        if v2_listings:
            listing = v2_listings[0]
            price_obj = listing.get("Price", {})

            # Current price: Price.Money.Amount
            money = price_obj.get("Money", {})
            current_price = money.get("Amount", 0.0)
            currency = money.get("Currency", "SAR")

            # Original price: Price.SavingBasis.Money.Amount
            saving_basis = price_obj.get("SavingBasis", {})
            sb_money = saving_basis.get("Money", {})
            original_price = sb_money.get("Amount", current_price)

            # Savings: Price.Savings.Money.Amount & Percentage
            savings_obj = price_obj.get("Savings", {})
            s_money = savings_obj.get("Money", {})
            savings_amount = s_money.get("Amount", 0.0)
            savings_percent = float(savings_obj.get("Percentage", 0))

        # Fallback to legacy Offers (if OffersV2 not present)
        if current_price == 0.0:
            legacy = item.get("Offers", {})
            legacy_listings = legacy.get("Listings", [])
            if legacy_listings:
                listing = legacy_listings[0]
                price_info = listing.get("Price", {})
                current_price = price_info.get("Amount", 0.0)
                currency = price_info.get("Currency", "SAR")
                saving_basis = listing.get("SavingBasis", {})
                original_price = saving_basis.get("Amount", current_price)

        # Prime — from legacy Offers DeliveryInfo (still the only source)
        for offers_key in ("Offers", "OffersV2"):
            offers_data = item.get(offers_key, {})
            for lst in offers_data.get("Listings", []):
                if lst.get("DeliveryInfo", {}).get("IsPrimeEligible", False):
                    is_prime = True
                    break

        # Ensure original >= current
        if original_price <= 0:
            original_price = current_price

        # Recalc savings if not provided by API
        if savings_amount <= 0 and original_price > current_price > 0:
            savings_amount = round(original_price - current_price, 2)
        if savings_percent <= 0 and original_price > 0 and savings_amount > 0:
            savings_percent = round((savings_amount / original_price) * 100, 1)

        # ── Deal details ────────────────────────────────────
        deal_badge = ""
        deal_end_time = ""
        is_deal = False
        offers_v2 = item.get("OffersV2", {})
        v2_listings = offers_v2.get("Listings", [])
        if v2_listings:
            deal = v2_listings[0].get("DealDetails", {})
            if deal:
                is_deal = True
                deal_badge = deal.get("Badge", "")
                deal_end_time = deal.get("EndTime", "")

        return Product(
            asin=asin,
            title=title,
            brand=brand,
            original_price=original_price,
            current_price=current_price,
            currency=currency,
            savings_percent=savings_percent,
            savings_amount=round(savings_amount, 2),
            features=features,
            image_url=image_url,
            affiliate_url=detail_url,
            is_prime=is_prime,
            keyword_used=keyword,
            deal_badge=deal_badge,
            deal_end_time=deal_end_time,
            is_deal=is_deal,
        )
