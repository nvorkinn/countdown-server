"""The broker's SQLite database: the SQLAlchemy and migration set-up, and bringing the schema up to date."""

import fcntl
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, override

from alembic import command
from alembic.script import ScriptDirectory
from flask import current_app
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import DateTime, event, inspect, text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

if TYPE_CHECKING:
    from flask import Flask
    from sqlalchemy.engine import Dialect
    from sqlalchemy.pool import ConnectionPoolEntry


class Base(DeclarativeBase):
    """The declarative base of every model in models.py."""


class UTCDateTime(TypeDecorator):
    """A timezone-aware UTC datetime column.

    SQLite keeps no time zone, so a value is stored as naive UTC and comes back with tzinfo=UTC; a naive
    datetime is refused rather than guessed at.
    """

    impl = DateTime
    cache_ok = True

    @override
    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            msg = f"naive datetime {value!r}: pass a timezone-aware one, e.g. datetime.now(UTC)"
            raise ValueError(msg)
        return value.astimezone(UTC).replace(tzinfo=None)

    @override
    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


db = SQLAlchemy(model_class=Base)
# What every model subclasses. db.Model is built at runtime (Base plus Flask-SQLAlchemy's table naming and binds), so
# type checkers can't follow it as a base class; to them it is Base, which it extends.
Model = Base if TYPE_CHECKING else db.Model
migrate = Migrate(directory=str(Path(__file__).resolve().parent / "migrations"), render_as_batch=True)


def _db_path() -> Path:
    return Path(os.environ.get("BROKER_DB_PATH", Path(__file__).resolve().parent.parent / "data" / "broker.db"))


def _enable_foreign_keys(dbapi_conn: sqlite3.Connection, _record: ConnectionPoolEntry) -> None:
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


def _applied_revisions() -> set[str]:
    """Return the revisions the DB is at, read straight from alembic_version; needs an app context.

    Not through Alembic's MigrationContext, which logs two INFO lines ("Context impl SQLiteImpl.") each time it is
    built.
    """
    with db.engine.connect() as conn:
        if not inspect(conn).has_table("alembic_version"):
            return set()
        return set(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())


# How many pre-migration copies of the database are kept beside it.
BACKUPS_KEPT = 5


def _back_up(applied: set[str]) -> None:
    """Copy the DB to `broker.db.bak-<time>-<revision it is at>` beside it, keeping the newest BACKUPS_KEPT.

    The way back if a migration, or the release that brings it, turns out wrong: a rollback is a redeploy, and an older
    image can't read a schema a newer one has changed. SQLite's backup API, so the copy is consistent even while another
    process writes. It lives on the same disk, so it guards against a bad migration, not a lost disk. A DB with no file
    yet, or an empty one, has nothing to lose.
    """
    path = _db_path()
    if not path.is_file() or path.stat().st_size == 0:
        return
    revision = "-".join(sorted(applied)) or "unversioned"
    backup = path.with_name(f"{path.name}.bak-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{revision}")
    source = sqlite3.connect(path)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    finally:
        target.close()
        source.close()
    current_app.logger.info("Backed up the database to %s before migrating it", backup.name)
    for old in sorted(path.parent.glob(f"{path.name}.bak-*"), key=lambda p: p.stat().st_mtime)[:-BACKUPS_KEPT]:
        old.unlink()
        current_app.logger.info("Removed the old backup %s", old.name)


def upgrade_db() -> None:
    """Bring the DB to the latest revision, under a file lock; needs an app context.

    Same as `flask db upgrade`, except it leaves logging alone: env.py's logging setup would silence the app's own
    logger. A DB already at head is left alone, so starting the app (every `flask` command does) logs nothing from
    Alembic unless a migration actually runs, and a copy of the DB is made (_back_up) only when one does.
    """
    with Path(f"{_db_path()}.migrate.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = current_app.extensions["migrate"].migrate.get_config()
        applied = _applied_revisions()
        if applied == set(ScriptDirectory.from_config(config).get_heads()):
            return
        _back_up(applied)
        config.attributes["app_startup"] = True
        command.upgrade(config, "head")


def init_read_only_app(app: Flask) -> None:
    """Point `app` at the broker's database for reading only: SQLite's mode=ro, and no migrations.

    For the glow and developer providers: a write raises, and none can hold the write lock the broker's own writes
    would wait for. It works with ./data mounted read-only because the database is in SQLite's default rollback-journal
    mode, where a reader only takes shared locks on the file and creates nothing next to it; in WAL mode every reader
    needs the -shm file, which it can't create on a read-only mount (tests/test_db.py pins the journal mode).
    """
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///file:{_db_path()}?mode=ro&uri=true"
    db.init_app(app)


def init_app(app: Flask) -> None:
    """Point `app` at the broker's database and bring it to the latest revision."""
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{_db_path()}"
    db.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401, PLC0415 -- registers the models on db.metadata (models imports db)

    with app.app_context():
        event.listen(db.engine, "connect", _enable_foreign_keys)
        upgrade_db()
