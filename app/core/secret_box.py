"""Encrypting platform tokens at rest, and signing short-lived OAuth state.

The key comes from ``INTEGRATIONS_SECRET_KEY`` (any long random string); it is
stretched with SHA-256 into a Fernet key (AES-128-CBC + HMAC-SHA256).
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretBoxError(Exception):
    """The key is missing, or a value can't be decrypted / verified."""


def _secret() -> bytes:
    key = get_settings().integrations_secret_key
    if not key:
        raise SecretBoxError("INTEGRATIONS_SECRET_KEY is not set.")
    return key.encode()


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_secret()).digest()))


def encrypt(value: str) -> str:
    """Encrypt a token for storage."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a stored token."""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretBoxError("Stored token can't be decrypted (was the key changed?).") from exc


def sign_state(data: dict[str, Any], ttl_seconds: int = 900) -> str:
    """A tamper-proof, expiring OAuth ``state`` value."""
    body = base64.urlsafe_b64encode(
        json.dumps({**data, "exp": int(time.time()) + ttl_seconds}).encode()
    ).decode()
    signature = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def read_state(state: str) -> dict[str, Any]:
    """Verify and decode a ``state`` value made by :func:`sign_state`."""
    body, _, signature = state.partition(".")
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise SecretBoxError("Invalid sign-in state.")
    data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body.encode()))
    if data.get("exp", 0) < time.time():
        raise SecretBoxError("The sign-in took too long. Please try again.")
    return data
