"""Engine, session factory, declarative Base and dialect-aware column types."""

from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, override

from sqlalchemy import Dialect, String, create_engine, event
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.config import get_settings


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetime.

    SQLite: ISO-8601 TEXT (e.g. ``2026-10-10T12:00:00.000000+00:00``), which sorts correctly.
    Postgres: ``TIMESTAMPTZ``. Values are always returned as aware UTC datetimes.
    """

    impl = String(32)
    cache_ok = True

    @override
    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[datetime]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(TIMESTAMP(timezone=True))
        return dialect.type_descriptor(String(32))

    @override
    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        value = value.astimezone(UTC)
        if dialect.name == "postgresql":
            return value
        return value.isoformat(timespec="microseconds")

    @override
    def process_result_value(
        self, value: str | datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base of every ORM model: its metadata is the schema Alembic migrates."""


@lru_cache
def get_engine() -> Engine:
    """The process's engine for ``SATLAB_DATABASE_URL``, created on first use.

    SQLite gets foreign keys and WAL on every connection; Postgres (Neon) gets a small pool that
    pre-pings and recycles connections, because the compute suspends when idle.
    """
    url = make_url(get_settings().database_url)
    if url.get_backend_name() == "sqlite":
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    # Postgres (Neon): the compute auto-suspends when idle and drops its connections, so check
    # connections on checkout and recycle them well before the server side goes away.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 10},
    )


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    """Session factory on the engine; objects stay usable after commit (no expiry)."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a DB session."""
    with get_sessionmaker()() as db:
        yield db
