"""Aggregate API router mounted by ``app.main`` under ``settings.api_prefix``."""
from fastapi import APIRouter

from app.api import auth, lessons, me, notes

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(lessons.router)
api_router.include_router(notes.router)

__all__ = ["api_router"]
