"""Password hashing and JWT access tokens.

Passwords are hashed with stdlib ``hashlib.scrypt`` and stored as a
self-describing string so the parameters can be rotated without a schema
change::

    scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>

Tokens are HS256 JWTs whose ``sub`` claim is the student id. The student id is
the only identity primitive the REST and WebSocket layers need.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings

# scrypt parameters. ``n`` must be a power of two. 2**14 keeps hashing around a
# few tens of milliseconds while remaining comfortably above interactive-gate
# recommendations for a teaching app (not a bank), with a 16 MiB memory cost.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

TOKEN_TYPE = "access"


def _b64e(raw: bytes) -> str:
    return raw.hex()


def _b64d(encoded: str) -> bytes:
    return bytes.fromhex(encoded)


def hash_password(password: str) -> str:
    """Hash a plaintext password with scrypt using a fresh random salt."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}"
        f"${_b64e(salt)}${_b64e(digest)}"
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification of a password against a stored hash.

    Returns ``False`` (never raises) for malformed hashes so an auth endpoint
    cannot be crashed by a corrupted row.
    """
    if not password or not encoded:
        return False
    try:
        algorithm, n_raw, r_raw, p_raw, salt_hex, hash_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = _b64d(salt_hex)
        expected = _b64d(hash_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def password_problem(password: str) -> str | None:
    """Return a human readable reason when ``password`` is too weak."""
    if not isinstance(password, str):
        return "password must be a string"
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"password must be at least {PASSWORD_MIN_LENGTH} characters long"
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"password must be at most {PASSWORD_MAX_LENGTH} characters long"
    has_letter = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    if not (has_letter and has_digit):
        return "password must contain at least one letter and one digit"
    return None


def create_access_token(student_id: str, expires_minutes: int | None = None) -> str:
    """Create a signed HS256 access token for ``student_id``."""
    if not student_id:
        raise ValueError("student_id is required")
    settings = get_settings()
    now = datetime.now(timezone.utc)
    minutes = settings.jwt_expires_minutes if expires_minutes is None else expires_minutes
    payload: dict = {
        "sub": str(student_id),
        "type": TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token_expiry() -> datetime:
    """UTC instant at which a freshly created access token expires."""
    settings = get_settings()
    return datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expires_minutes)


def decode_access_token(token: str) -> str | None:
    """Validate a token and return its subject (student id), else ``None``.

    Never raises: expired, malformed, wrongly signed or non-access tokens all
    resolve to ``None`` so callers can map them to a single 401 envelope.
    """
    if not token or not isinstance(token, str):
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != TOKEN_TYPE:
        return None
    subject = payload.get("sub")
    if not subject or not isinstance(subject, str):
        return None
    return subject
