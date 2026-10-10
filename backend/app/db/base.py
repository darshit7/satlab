"""Engine, session factory, declarative Base and dialect-aware column types."""

from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import Dialect, String, create_engine, event
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.config import get_settings


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetime.

    SQLite: ISO-8601 TEXT (e.g. ``2026-10-10T12:00:00.000000+00:00``), which sorts correctly.
    Postgres (Phase 7): ``TIMESTAMPTZ``. Values are always returned as aware UTC datetimes.
    """

    impl = String(32)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[datetime]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(TIMESTAMP(timezone=True))
        return dialect.type_descriptor(String(32))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        value = value.astimezone(UTC)
        if dialect.name == "postgresql":
            return value
        return value.isoformat(timespec="microseconds")

    def process_result_value(
        self, value: str | datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a DB session."""
    with get_sessionmaker()() as db:
        yield db
