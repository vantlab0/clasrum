"""Student profile endpoints: GET/PATCH /me and DELETE /me/data (§5)."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.contracts import dtos
from app.contracts.mappers import student_to_dto
from app.core import errors
from app.core.deps import CurrentStudent, DbSession
from app.db import repositories
from app.graph import service as graph_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me", tags=["profile"])


@router.get("", response_model=dtos.Student)
async def get_me(student: CurrentStudent) -> dtos.Student:
    return student_to_dto(student)


@router.patch("", response_model=dtos.Student)
async def update_me(
    payload: dtos.UpdateMeRequest,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.Student:
    fields: dict = {}
    if payload.display_name is not None:
        fields["display_name"] = payload.display_name
    if payload.default_level is not None:
        fields["default_level"] = payload.default_level
    if payload.default_duration_minutes is not None:
        fields["default_duration_minutes"] = payload.default_duration_minutes
    if payload.preferences is not None:
        merged = dict(student.preferences or {})
        merged.update(payload.preferences)
        fields["preferences"] = merged
    if fields:
        student = await repositories.update_user(db, student, **fields)
    if payload.default_level is not None or payload.default_duration_minutes is not None:
        await repositories.upsert_profile(
            db,
            student.id,
            level=payload.default_level,
            preferred_duration_minutes=payload.default_duration_minutes,
        )
    return student_to_dto(student)


@router.delete("/data", response_model=dtos.DeleteDataResponse)
async def delete_my_data(
    payload: dtos.DeleteDataRequest,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.DeleteDataResponse:
    """Erase every row owned by the student plus their LangGraph memory.

    ``confirm`` must be exactly ``true`` (a missing/false value is a 422 so a
    stray request can never wipe data).
    """
    if payload.confirm is not True:
        raise errors.invalid_arguments(
            "confirm must be true to delete all data",
            details={"field": "confirm"},
        )
    student_id = student.id
    try:
        await graph_service.delete_student_memory(student_id)
    except Exception:  # graph memory cleanup must never block the DB erasure
        logger.warning(
            "LangGraph memory cleanup failed for student %s; continuing with "
            "relational cascade",
            student_id,
            exc_info=True,
        )
    await repositories.delete_student_data(db, student_id)
    logger.info("deleted all data for student %s", student_id)
    return dtos.DeleteDataResponse(deleted=True)
