"""Password hashing and session-token helpers.

* Passwords: argon2id (``argon2-cffi`` defaults), re-hashed automatically when
  the parameters change.
* Session tokens: 256-bit random values. Only their SHA-256 hash is stored, so
  a database leak does not expose live sessions.
"""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Verified against when the username doesn't exist, so a failed login takes the
# same time whether or not the account exists (no username enumeration).
_DUMMY_HASH = _hasher.hash("teg-crm-timing-equaliser")

PASSWORD_MIN_LENGTH = 10


def hash_password(password: str) -> str:
    """Return an argon2id hash of ``password``."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check ``password`` against ``password_hash`` in constant-ish time.

    Args:
        password: Plain-text password from the login form.
        password_hash: Stored hash, or ``None`` for accounts without a password.

    Returns:
        ``True`` only when the password matches.
    """
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether the stored hash uses outdated argon2 parameters."""
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, *, username: str) -> list[str]:
    """Return human-readable reasons the password is too weak (empty list = OK)."""
    problems = []
    if len(password) < PASSWORD_MIN_LENGTH:
        problems.append(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    if password.lower() == username.lower() or username.lower() in password.lower():
        problems.append("Don't include your username.")
    if len(set(password)) < 4:
        problems.append("Use a mix of different characters.")
    return problems


def new_token() -> str:
    """Return a new URL-safe 256-bit random token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest stored for ``token``."""
    return hashlib.sha256(token.encode()).hexdigest()
