"""Alembic revisions apply, roll back and match the ORM (SQLite; Postgres when opted in)."""

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect

from app.config import BACKEND_DIR
from app.db.base import get_engine


def _alembic() -> AlembicConfig:
    return AlembicConfig(str(BACKEND_DIR / "alembic.ini"))


def _tables() -> set[str]:
    return set(inspect(get_engine()).get_table_names())


def test_upgrade_downgrade_roundtrip(app_env: Path) -> None:
    cfg = _alembic()
    command.upgrade(cfg, "head")
    assert {"ground_station", "satellite", "mission_session", "pass_window"} <= _tables()

    command.downgrade(cfg, "base")
    assert _tables() <= {"alembic_version"}

    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if the ORM models have drifted from the migrations
