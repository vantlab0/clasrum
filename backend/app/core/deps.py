"""FastAPI dependencies: bearer authentication and lesson ownership checks."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.security import decode_access_token
from app.db.models import Lesson, User
from app.db.session import get_db

# ``auto_error=False`` so missing/invalid credentials always flow through our
# own handler and produce the stable ``unauthorized`` error envelope.
_bearer = HTTPBearer(auto_error=False, description="JWT access token")

BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def resolve_student(db: AsyncSession, token: str | None) -> User:
    """Resolve a raw bearer token to an active user or raise 401."""
    if not token:
        raise errors.unauthorized("Missing bearer token")
    student_id = decode_access_token(token)
    if not student_id:
        raise errors.unauthorized("Invalid or expired token")
    student = await db.get(User, student_id)
    if student is None or not student.is_active:
        raise errors.unauthorized("Account not found or inactive")
    return student


async def get_current_student(credentials: BearerCredentials, db: DbSession) -> User:
    """Authenticated student for REST endpoints (401 on failure)."""
    token = credentials.credentials if credentials is not None else None
    return await resolve_student(db, token)


CurrentStudent = Annotated[User, Depends(get_current_student)]


async def require_lesson_owner(
    lesson_id: str,
    student: User,
    db: AsyncSession,
) -> Lesson:
    """Load a lesson enforcing ownership: 404 when missing, 403 when foreign.

    404 is returned before ownership is considered so a student cannot probe
    which lesson ids exist; a lesson that exists but belongs to somebody else
    yields 403 (the client should not have been given the id in the first
    place, and a silent 404 would hide real contract bugs).
    """
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise errors.not_found("Lesson not found")
    if lesson.student_id != student.id:
        raise errors.forbidden("Lesson belongs to another student")
    return lesson
