"""Optional Supabase JWT verification and identity mapping.

This keeps the existing backend auth model (local `users` + ownership checks)
while allowing Supabase-issued access tokens to authenticate requests when the
project is configured with Supabase credentials.

Supports:
- JWKS verification via `SUPABASE_JWKS_URL` (recommended for asymmetric keys)
- HS256 verification via `SUPABASE_JWT_SECRET` (legacy project JWT secret)
"""

from __future__ import annotations

import secrets
import threading
from typing import Any

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import hash_password
from app.db import repositories
from app.db.models import StudentLearningProfile, User

_jwks_client: jwt.PyJWKClient | None = None
_jwks_client_url: str | None = None
_jwks_lock = threading.Lock()


def _get_jwks_client(url: str) -> jwt.PyJWKClient | None:
    global _jwks_client, _jwks_client_url
    url = (url or "").strip()
    if not url:
        return None
    with _jwks_lock:
        if _jwks_client is None or _jwks_client_url != url:
            # cache_jwk_set reduces repeated fetches; lifespan is handled by PyJWT client.
            _jwks_client = jwt.PyJWKClient(url, cache_jwk_set=True, lifespan=300)
            _jwks_client_url = url
        return _jwks_client


def _decode_with_jwks(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    url = (getattr(settings, "supabase_jwks_url", "") or "").strip()
    client = _get_jwks_client(url)
    if client is None:
        return None
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256", "HS256"],
            audience=getattr(settings, "supabase_jwt_audience", "authenticated")
            or "authenticated",
            options={"require": ["exp", "sub"]},
        )
    except Exception:
        return None
    return _validate_supabase_payload(payload)


def _decode_with_hs256_secret(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    secret = (settings.supabase_jwt_secret or "").strip()
    if not secret:
        return None
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=settings.supabase_jwt_audience or "authenticated",
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    return _validate_supabase_payload(payload)


def _validate_supabase_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        return None
    # Supabase access tokens typically include role/aud; keep checks permissive
    # but require authenticated user context.
    role = payload.get("role")
    if role is not None and role != "authenticated":
        return None
    return payload


def decode_supabase_access_token(token: str) -> dict[str, Any] | None:
    """Decode a Supabase access token if Supabase verification is configured.

    Tries JWKS first (when configured), then HS256 project JWT secret.
    Returns the verified payload or None when unconfigured/invalid.
    Never raises.
    """
    if not token or not isinstance(token, str):
        return None
    payload = _decode_with_jwks(token)
    if payload is not None:
        return payload
    return _decode_with_hs256_secret(token)


async def get_or_create_user_for_supabase_payload(
    db: AsyncSession, payload: dict[str, Any]
) -> User | None:
    """Resolve or provision a local user for a verified Supabase identity.

    We prefer using Supabase `sub` as the local user id (both UUID strings) to
    preserve stable ownership mapping. Never trusts client-supplied user ids;
    identity comes only from the verified token payload.
    """
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        return None
    user = await repositories.get_user(db, sub)
    if user is not None:
        return user if user.is_active else None

    email = payload.get("email")
    if not isinstance(email, str) or not email.strip():
        # Email may be absent depending on provider/token; use a stable synthetic
        # address for local uniqueness/compatibility.
        email = f"{sub}@supabase.local"
    email = email.strip().lower()

    existing_by_email = await repositories.get_user_by_email(db, email)
    if existing_by_email is not None:
        return existing_by_email if existing_by_email.is_active else None

    meta = payload.get("user_metadata")
    display_name = "Student"
    if isinstance(meta, dict):
        candidate = (
            meta.get("display_name") or meta.get("full_name") or meta.get("name")
        )
        if isinstance(candidate, str) and candidate.strip():
            display_name = candidate.strip()[:120]

    # Local schema requires a password hash; generate an unusable random one.
    unusable_password = secrets.token_urlsafe(48)
    user = User(
        id=sub,
        email=email,
        password_hash=hash_password(unusable_password),
        display_name=display_name,
        default_level="high_school",
        default_duration_minutes=20,
        preferences={},
        is_active=True,
    )
    db.add(user)
    await db.flush()
    # Ensure companion learning profile exists, consistent with local registration.
    db.add(
        StudentLearningProfile(
            student_id=user.id,
            level="high_school",
            preferred_duration_minutes=20,
            subjects_studied=[],
            strengths=[],
            weaknesses=[],
        )
    )
    await db.flush()
    return user
