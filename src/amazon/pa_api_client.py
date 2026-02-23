"""Amazon Product Advertising API 5.0 client with AWS Signature V4 auth."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from loguru import logger


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AmazonAPIError(Exception):
    """Raised when the PA API returns a non-200 response."""

    pass


class AmazonAPIThrottled(AmazonAPIError):
    """Raised when the PA API returns HTTP 429 (Too Many Requests)."""

    pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AmazonAPIConfig:
    """Holds credentials and endpoint settings for the PA API 5.0.

    Attributes:
        access_key: AWS access key for the PA API.
        secret_key: AWS secret key for the PA API.
        partner_tag: Amazon Associates partner tag.
        host: PA API endpoint host.
        region: AWS region for request signing.
        marketplace: Amazon marketplace domain.
    """

    access_key: str
    secret_key: str
    partner_tag: str
    host: str = "webservices.amazon.com"
    region: str = "us-east-1"
    marketplace: str = "www.amazon.com"


# ---------------------------------------------------------------------------
# Resources requested in every call
# ---------------------------------------------------------------------------

_DEFAULT_RESOURCES: list[str] = [
    "Images.Primary.Large",
    "ItemInfo.Title",
    "ItemInfo.Features",
    "ItemInfo.ByLineInfo",
    "Offers.Listings.Price",
    "Offers.Listings.SavingBasis",
    "Offers.Listings.MerchantInfo",
    "Offers.Listings.Condition",
    "Offers.Listings.DeliveryInfo.IsPrimeEligible",
    "BrowseNodeInfo.BrowseNodes",
]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AmazonPAAPIClient:
    """Async client for the Amazon Product Advertising API 5.0.

    Uses ``httpx.AsyncClient`` for HTTP and implements full AWS Signature
    Version 4 request signing.

    Args:
        config: An ``AmazonAPIConfig`` with credentials and endpoint info.
    """

    SERVICE = "ProductAdvertisingAPI"

    def __init__(self, config: AmazonAPIConfig) -> None:
        self.config = config
        self._client = httpx.AsyncClient(timeout=30.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_items(
        self,
        keywords: str,
        search_index: str = "All",
        item_count: int = 10,
        min_saving_percent: Optional[int] = None,
        sort_by: str = "Featured",
    ) -> dict:
        """Search for products matching *keywords*.

        Args:
            keywords: Search query string.
            search_index: Amazon search index (category).
            item_count: Number of results (max 10).
            min_saving_percent: Optional minimum discount filter.
            sort_by: Sort strategy (e.g. ``"Featured"``).

        Returns:
            Parsed JSON response from the PA API.
        """
        payload: dict[str, Any] = {
            "Keywords": keywords,
            "SearchIndex": search_index,
            "ItemCount": min(item_count, 10),
            "PartnerTag": self.config.partner_tag,
            "PartnerType": "Associates",
            "Marketplace": self.config.marketplace,
            "Resources": _DEFAULT_RESOURCES,
            "SortBy": sort_by,
        }

        if min_saving_percent is not None:
            payload["MinSavingPercent"] = min_saving_percent

        logger.debug(
            "SearchItems — keywords='{}', index={}, count={}",
            keywords,
            search_index,
            item_count,
        )
        return await self._make_request("SearchItems", payload)

    async def get_items(self, asins: list[str]) -> dict:
        """Fetch product details by ASIN.

        Args:
            asins: List of ASINs to look up (max 10).

        Returns:
            Parsed JSON response from the PA API.
        """
        payload: dict[str, Any] = {
            "ItemIds": asins[:10],
            "PartnerTag": self.config.partner_tag,
            "PartnerType": "Associates",
            "Marketplace": self.config.marketplace,
            "Resources": _DEFAULT_RESOURCES,
        }

        logger.debug("GetItems — asins={}", asins[:10])
        return await self._make_request("GetItems", payload)

    # ------------------------------------------------------------------
    # Request execution
    # ------------------------------------------------------------------

    async def _make_request(self, operation: str, payload: dict) -> dict:
        """Sign and send a request to the PA API.

        Args:
            operation: API operation name (e.g. ``"SearchItems"``).
            payload: JSON-serialisable request body.

        Returns:
            Parsed JSON response.

        Raises:
            AmazonAPIThrottled: On HTTP 429.
            AmazonAPIError: On any other non-200 status.
        """
        target = (
            f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{operation}"
        )
        path = f"/paapi5/{operation.lower()}"
        url = f"https://{self.config.host}{path}"
        body = json.dumps(payload)

        timestamp = datetime.now(timezone.utc)
        headers = self._sign_request("POST", path, body, target, timestamp)

        response = await self._client.post(url, content=body, headers=headers)

        if response.status_code == 200:
            data = response.json()
            # Log item count from response when available
            items = (
                data.get("SearchResult", {}).get("Items", [])
                or data.get("ItemsResult", {}).get("Items", [])
            )
            logger.info(
                "{} succeeded — {} items returned", operation, len(items)
            )
            return data

        if response.status_code == 429:
            logger.warning("PA API throttled (429) on {}", operation)
            raise AmazonAPIThrottled(
                f"Request throttled by Amazon PA API during {operation}"
            )

        snippet = response.text[:200]
        logger.error(
            "PA API error on {} — status {}: {}",
            operation,
            response.status_code,
            snippet,
        )
        raise AmazonAPIError(
            f"[{response.status_code}] {snippet}"
        )

    # ------------------------------------------------------------------
    # AWS Signature V4
    # ------------------------------------------------------------------

    def _sign_request(
        self,
        method: str,
        path: str,
        body: str,
        target: str,
        timestamp: datetime,
    ) -> dict[str, str]:
        """Create AWS Signature V4 signed headers.

        Args:
            method: HTTP method (``"POST"``).
            path: Request path (e.g. ``"/paapi5/searchitems"``).
            body: Serialised JSON body.
            target: ``x-amz-target`` header value.
            timestamp: Current UTC time for the signature.

        Returns:
            A dict of headers ready to attach to the request.
        """
        amz_date = timestamp.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = timestamp.strftime("%Y%m%d")

        # Headers that will be signed (must be sorted by key)
        headers: dict[str, str] = {
            "content-encoding": "amz-1.0",
            "content-type": "application/json; charset=utf-8",
            "host": self.config.host,
            "x-amz-date": amz_date,
            "x-amz-target": target,
        }

        sorted_keys = sorted(headers.keys())
        canonical_headers = "".join(
            f"{k}:{headers[k]}\n" for k in sorted_keys
        )
        signed_headers = ";".join(sorted_keys)
        payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

        # Step 1 — Canonical request
        canonical_request = "\n".join(
            [
                method,
                path,
                "",  # empty query string
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )

        # Step 2 — String to sign
        credential_scope = (
            f"{date_stamp}/{self.config.region}/{self.SERVICE}/aws4_request"
        )
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(
                    canonical_request.encode("utf-8")
                ).hexdigest(),
            ]
        )

        # Step 3 — Signing key
        signing_key = self._get_signature_key(
            self.config.secret_key, date_stamp, self.config.region
        )

        # Step 4 — Signature
        signature = hmac.new(
            signing_key,
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Step 5 — Authorization header
        authorization = (
            f"AWS4-HMAC-SHA256 "
            f"Credential={self.config.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

        headers["Authorization"] = authorization
        return headers

    @staticmethod
    def _get_signature_key(
        key: str, date_stamp: str, region: str
    ) -> bytes:
        """Derive the AWS Signature V4 signing key.

        Args:
            key: AWS secret access key.
            date_stamp: Date in ``YYYYMMDD`` format.
            region: AWS region string.

        Returns:
            The derived signing key as raw bytes.
        """

        def _sign(k: bytes, msg: str) -> bytes:
            return hmac.new(k, msg.encode("utf-8"), hashlib.sha256).digest()

        date_key = _sign(f"AWS4{key}".encode("utf-8"), date_stamp)
        region_key = _sign(date_key, region)
        service_key = _sign(region_key, "ProductAdvertisingAPI")
        signing_key = _sign(service_key, "aws4_request")
        return signing_key

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        try:
            await self._client.aclose()
            logger.debug("PA API client closed")
        except Exception:
            logger.exception("Error closing PA API client")
