"""Authentication endpoints: register + login (§5)."""
from __future__ import annotations

from fastapi import APIRouter

from app.contracts import dtos
from app.contracts.mappers import student_to_dto
from app.core import errors
from app.core.deps import DbSession
from app.core.security import (
    create_access_token,
    hash_password,
    password_problem,
    verify_password,
)
from app.db import repositories

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=dtos.TokenResponse)
async def register(payload: dtos.RegisterRequest, db: DbSession) -> dtos.TokenResponse:
    """Create a student account and return a bearer token.

    * invalid email / weak password -> 422 ``invalid_arguments`` (also covers
      pydantic body validation which the app handler maps to the same code)
    * duplicate email -> 409 ``conflict``
    """
    problem = password_problem(payload.password)
    if problem is not None:
        raise errors.invalid_arguments(problem, details={"field": "password"})
    existing = await repositories.get_user_by_email(db, payload.email)
    if existing is not None:
        raise errors.conflict(
            "An account with this email already exists",
            details={"field": "email"},
        )
    user = await repositories.create_user(
        db,
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    token = create_access_token(user.id)
    return dtos.TokenResponse(
        access_token=token,
        token_type="bearer",
        student=student_to_dto(user),
    )


@router.post("/login", response_model=dtos.TokenResponse)
async def login(payload: dtos.LoginRequest, db: DbSession) -> dtos.TokenResponse:
    user = await repositories.get_user_by_email(db, payload.email)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise errors.unauthorized("Invalid email or password")
    if not user.is_active:
        raise errors.unauthorized("This account is inactive")
    token = create_access_token(user.id)
    return dtos.TokenResponse(
        access_token=token,
        token_type="bearer",
        student=student_to_dto(user),
    )
