"""Persistence selection for LangGraph: Postgres (prod) / SQLite (dev+test) / in-memory.

Three collabours are provided as one async context manager:

``checkpointer``
    ``AsyncPostgresSaver`` when ``database_url`` is a Postgres DSN,
    ``AsyncSqliteSaver`` when it is a SQLite DSN (dev/tests, file-backed so a
    *new* process/instance resumes the same thread), ``InMemorySaver`` only as a
    clearly-logged fallback when the LangGraph packages are unavailable.

``store``
    ``AsyncPostgresStore`` for Postgres, ``InMemoryStore`` otherwise. The Store
    holds long-term memory docs (namespaces ``("students", id, "profile" |
    "concepts" | "observations")``); the relational tables
    ``student_learning_profiles`` / ``student_concept_mastery`` /
    ``student_learning_observations`` are the queryable mirror written by
    :mod:`app.graph.memory`. Ownership: Store = primary document store,
    relational tables = queryable mirror that the graph actually reads on every
    turn (so behaviour is identical when the Store is in-memory).

``kind``
    ``"postgres" | "sqlite" | "memory"`` — callers use it to decide whether the
    graph checkpoint is guaranteed durable (see ``app.graph.service``).

No module-level imports of the Postgres driver: importing
``langgraph.checkpoint.postgres`` requires ``psycopg`` + libpq, which is not
needed for SQLite dev/test and must not crash the app at import time.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

POSTGRES_KIND = "postgres"
SQLITE_KIND = "sqlite"
MEMORY_KIND = "memory"


@dataclass
class LessonPersistence:
    """Bundle returned by :func:`lesson_persistence`."""

    checkpointer: Any
    store: Any
    kind: str
    closers: list[Any] = field(default_factory=list)

    @property
    def durable(self) -> bool:
        """True when checkpoints survive a new process/instance."""
        return self.kind in {POSTGRES_KIND, SQLITE_KIND}

    async def aclose(self) -> None:
        for closer in reversed(self.closers):
            try:
                await closer()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("persistence closer failed", exc_info=True)
        self.closers.clear()


def configured_kind(settings: Settings | None = None) -> str:
    """Which persistence backend the configuration selects (no driver import)."""
    settings = settings or get_settings()
    url = (settings.database_url or "").lower()
    if url.startswith("postgres"):
        return POSTGRES_KIND
    if url.startswith("sqlite"):
        return SQLITE_KIND
    return MEMORY_KIND


def _ensure_aiosqlite_is_alive() -> None:
    """Shim for ``langgraph-checkpoint-sqlite`` <= 2.0.11.

    That release calls ``aiosqlite.Connection.is_alive()`` in ``setup()``, a
    helper aiosqlite never shipped. Without it every SQLite checkpoint run
    raises ``'Connection' object has no attribute 'is_alive'`` and silently
    falls back to volatile in-memory persistence. The connection has already
    been opened by ``from_conn_string`` before ``setup()`` runs, so reporting
    it alive is accurate.
    """
    import aiosqlite

    if not hasattr(aiosqlite.Connection, "is_alive"):
        aiosqlite.Connection.is_alive = lambda self: True  # type: ignore[attr-defined]


def _sqlite_path(database_url: str) -> str:
    """Pick a *separate* SQLite file for LangGraph checkpoints.

    The application database is written inside an open SQLAlchemy transaction
    during a graph run, so sharing the same file makes the checkpointer fail
    with ``database is locked``. Sibling file (<db>-checkpoints.db) keeps the
    WAL writers apart while staying next to the data it belongs to.
    """
    from sqlalchemy.engine import make_url

    try:
        url = make_url(database_url)
        database = url.database or ""
    except Exception:  # pragma: no cover - malformed DSN, let sqlite create ./checkpoints.db
        logger.warning("could not parse sqlite database_url; falling back to checkpoints.db")
        return "checkpoints.db"
    if not database or database == ":memory:":
        return ":memory:"
    stem, dot, ext = database.rpartition(".")
    if not dot:
        return f"{database}-checkpoints.db"
    return f"{stem}-checkpoints.{ext}"


def _postgres_dsn(database_url: str) -> str:
    """Render a psycopg-compatible DSN (``postgresql://...``), no async driver."""
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    url = url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _memory_persistence() -> LessonPersistence:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    return LessonPersistence(
        checkpointer=InMemorySaver(), store=InMemoryStore(), kind=MEMORY_KIND
    )


@contextlib.asynccontextmanager
async def lesson_persistence(
    settings: Settings | None = None, *, require_durable: bool = False
) -> AsyncIterator[LessonPersistence]:
    """Open checkpointer + Store for one graph run (per lesson/action)."""
    settings = settings or get_settings()
    kind = configured_kind(settings)

    if kind == POSTGRES_KIND:
        stack = contextlib.AsyncExitStack()
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from langgraph.store.postgres.aio import AsyncPostgresStore

            dsn = _postgres_dsn(settings.database_url)
            saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(dsn))
            await saver.setup()
            store = await stack.enter_async_context(AsyncPostgresStore.from_conn_string(dsn))
            setup_store = getattr(store, "setup", None)
            if setup_store is not None:
                await setup_store()
            logger.info("LangGraph persistence: postgres (durable checkpoints + store)")
            try:
                yield LessonPersistence(
                    checkpointer=saver,
                    store=store,
                    kind=POSTGRES_KIND,
                    closers=[stack.aclose],
                )
            finally:
                await stack.aclose()
            return
        except Exception as exc:  # noqa: BLE001 - import/runtime driver problems
            await stack.aclose()
            message = (
                "LangGraph Postgres persistence is unavailable "
                f"(database_url is Postgres but the psycopg/checkpointer stack failed: {exc}). "
                "Install 'psycopg[binary]' or run with SQLite for dev."
            )
            logger.error(message)
            if require_durable:
                raise RuntimeError(message) from exc
            logger.warning("Falling back to in-memory LangGraph persistence for this run")
            persistence = _memory_persistence()
            try:
                yield persistence
            finally:
                await persistence.aclose()
            return

    if kind == SQLITE_KIND:
        _ensure_aiosqlite_is_alive()
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.store.memory import InMemoryStore

        path = _sqlite_path(settings.database_url)
        stack = contextlib.AsyncExitStack()
        try:
            saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(path))
            await saver.setup()
            logger.info("LangGraph persistence: sqlite (%s)", path)
            try:
                yield LessonPersistence(
                    checkpointer=saver,
                    store=InMemoryStore(),
                    kind=SQLITE_KIND,
                    closers=[stack.aclose],
                )
            finally:
                await stack.aclose()
            return
        except Exception as exc:  # noqa: BLE001 - missing package / locked file
            await stack.aclose()
            logger.error("LangGraph SQLite persistence unavailable: %s", exc)
            if require_durable:
                raise
            persistence = _memory_persistence()
            try:
                yield persistence
            finally:
                await persistence.aclose()
            return

    logger.warning(
        "LangGraph persistence: in-memory fallback (database_url=%r is not Postgres/SQLite)",
        settings.database_url,
    )
    persistence = _memory_persistence()
    try:
        yield persistence
    finally:
        await persistence.aclose()


async def init_persistence(settings: Settings | None = None) -> str:
    """Create/validate checkpoint + Store tables (FastAPI lifespan startup).

    Idempotent; async; no args required. Returns the backend kind
    (``"postgres"``, ``"sqlite"`` or ``"memory"``). Raises when Postgres is
    configured and the driver stack genuinely cannot be initialised — a
    production deployment must not silently lose durable checkpoints.
    """
    settings = settings or get_settings()
    kind = configured_kind(settings)
    async with lesson_persistence(settings, require_durable=(kind == POSTGRES_KIND)):
        logger.info("LangGraph persistence initialised: %s", kind)
    return kind


async def shutdown_persistence() -> None:
    """Close any cached persistence resources (none are cached today).

    Present so the FastAPI lifespan can call it symmetrically and so a future
    connection-pool cache has one obvious shutdown point.
    """
    logger.debug("LangGraph persistence shutdown (no cached resources)")


__all__ = [
    "LessonPersistence",
    "MEMORY_KIND",
    "POSTGRES_KIND",
    "SQLITE_KIND",
    "configured_kind",
    "init_persistence",
    "lesson_persistence",
    "shutdown_persistence",
]
