"""Liveness/readiness endpoint (§5)."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.contracts import dtos
from app.core.deps import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=dtos.HealthResponse)
async def healthz(db: DbSession):
    """Report process, database and Qwen configuration state."""
    settings = get_settings()
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        logger.exception("healthz database check failed")
        payload = dtos.HealthResponse(
            status="degraded",
            version=settings.version,
            db="error",
            qwen_configured=settings.qwen_configured,
        )
        return JSONResponse(status_code=503, content=payload.model_dump(mode="json"))
    return dtos.HealthResponse(
        status="ok",
        version=settings.version,
        db=db_status,
        qwen_configured=settings.qwen_configured,
    )
