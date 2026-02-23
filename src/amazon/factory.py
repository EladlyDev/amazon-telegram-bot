"""Factory for creating the appropriate Amazon data-source client.

Selects between the PA API client and the web scraper based on
configuration and credential availability.
"""

from __future__ import annotations

import logging

from src.amazon.base import AmazonClient
from src.amazon.pa_api import PAAPIClient
from src.amazon.scraper import AmazonScraper

logger = logging.getLogger(__name__)


def create_amazon_client(
    source: str,
    access_key: str = "",
    secret_key: str = "",
    partner_tag: str = "",
) -> AmazonClient:
    """Create the appropriate Amazon client based on the data source setting.

    Args:
        source: ``"pa_api"`` or ``"scraper"``.
        access_key: PA API access key.
        secret_key: PA API secret key.
        partner_tag: Amazon Associates partner tag.

    Returns:
        An :class:`AmazonClient` implementation.
    """
    if source == "pa_api" and access_key and secret_key and partner_tag:
        logger.info("Using PA API client (tag=%s).", partner_tag)
        return PAAPIClient(access_key, secret_key, partner_tag)

    if source == "pa_api":
        logger.warning(
            "PA API requested but credentials are incomplete. "
            "Falling back to web scraper."
        )

    logger.info("Using web scraper client (tag=%s).", partner_tag or "none")
    return AmazonScraper(partner_tag)
