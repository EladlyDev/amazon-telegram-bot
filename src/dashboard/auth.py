"""Authentication utilities for the dashboard.

Provides PBKDF2-SHA256 password hashing, session ID generation and signing,
OTP/recovery key generation, User-Agent parsing, and IP extraction.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session_id"
SESSION_MAX_AGE = 86400  # 24 hours in seconds
OTP_EXPIRY_SECONDS = 300  # 5 minutes
OTP_LENGTH = 6
LOCKOUT_THRESHOLD = 5  # failed attempts before lockout
LOCKOUT_DURATION_MINUTES = 15


# ── Password Hashing (PBKDF2-SHA256, 100K iterations) ──────


def hash_password(password: str) -> str:
    """Hash *password* using PBKDF2-SHA256 with a random 32-byte salt.

    Returns:
        ``"{salt_hex}:{key_hex}"`` format string.
    """
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 100_000
    )
    return f"{salt.hex()}:{key.hex()}"


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify *plain_password* against a PBKDF2 hash.

    Also handles backward-compatible plain-text passwords (for the
    initial migration from ``.env``-only auth).  Uses constant-time
    comparison to prevent timing attacks.
    """
    if not plain_password or not password_hash:
        return False
    try:
        # PBKDF2 format: "{salt_hex}:{key_hex}" — always 129+ chars
        if ":" in password_hash and len(password_hash) > 100:
            salt_hex, key_hex = password_hash.split(":", 1)
            salt = bytes.fromhex(salt_hex)
            expected_key = bytes.fromhex(key_hex)
            actual_key = hashlib.pbkdf2_hmac(
                "sha256", plain_password.encode("utf-8"), salt, 100_000
            )
            return secrets.compare_digest(actual_key, expected_key)
        # Fallback: plain-text comparison (first run before migration)
        return secrets.compare_digest(
            plain_password.encode(), password_hash.encode()
        )
    except (ValueError, AttributeError):
        return False


# ── OTP / Recovery Key Generation ──────────────────────────


def generate_otp() -> str:
    """Generate a cryptographically random 6-digit OTP."""
    return "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))


def generate_recovery_key() -> str:
    """Generate a master recovery key in ``XXXX-XXXX-XXXX-XXXX`` format.

    Shown **once** to the admin — must be saved securely offline.
    """
    raw = secrets.token_hex(8).upper()
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


def generate_session_id() -> str:
    """Generate a cryptographically random 64-char hex session ID."""
    return secrets.token_hex(32)


# ── Session Cookie Signer ──────────────────────────────────


class SessionSigner:
    """Signs and verifies session IDs in cookies using *itsdangerous*."""

    def __init__(self, secret_key: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key)

    def sign_session_id(self, session_id: str) -> str:
        """Sign *session_id* for storage in a cookie."""
        return self._serializer.dumps({"sid": session_id})

    def unsign_session_id(self, token: str) -> str | None:
        """Extract the session ID from a signed cookie value.

        Returns ``None`` if the token is invalid, tampered, or expired.
        """
        if not token:
            return None
        try:
            data = self._serializer.loads(token, max_age=SESSION_MAX_AGE)
            return data.get("sid")
        except (SignatureExpired, BadSignature):
            return None


session_signer = SessionSigner(settings.dashboard_secret_key)


# ── User-Agent Parser ──────────────────────────────────────


def parse_user_agent(ua_string: str) -> str:
    """Parse a User-Agent string into a human-readable summary.

    Returns something like ``"Chrome on Windows"`` or ``"Safari on iPhone"``.
    Uses simple substring matching — no external library needed.
    """
    if not ua_string:
        return "Unknown"

    ua = ua_string.lower()

    # Browser detection (order matters — Edge/Opera contain "chrome")
    if "edg/" in ua or "edge/" in ua:
        browser = "Edge"
    elif "opr/" in ua or "opera" in ua:
        browser = "Opera"
    elif "chrome" in ua:
        browser = "Chrome"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "safari" in ua:
        browser = "Safari"
    elif "msie" in ua or "trident" in ua:
        browser = "Internet Explorer"
    else:
        browser = "Unknown Browser"

    # OS detection
    if "iphone" in ua:
        os_name = "iPhone"
    elif "ipad" in ua:
        os_name = "iPad"
    elif "android" in ua:
        os_name = "Android"
    elif "mac os" in ua or "macintosh" in ua:
        os_name = "macOS"
    elif "windows" in ua:
        os_name = "Windows"
    elif "linux" in ua:
        os_name = "Linux"
    elif "cros" in ua:
        os_name = "ChromeOS"
    else:
        os_name = "Unknown OS"

    return f"{browser} on {os_name}"


# ── Request Helpers ────────────────────────────────────────


def get_client_ip(request) -> str:
    """Extract the real client IP from a request.

    Checks ``X-Forwarded-For`` (set by Nginx / reverse proxies) first,
    then falls back to ``request.client.host``.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
