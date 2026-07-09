"""SQLAlchemy engine/session setup for the SQLite (v1) database."""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import database_url


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            database_url(),
            connect_args={"check_same_thread": False},
            future=True,
        )
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


def init_db() -> None:
    from app import models  # noqa: F401  (register models)

    engine = get_engine()
    Base.metadata.create_all(engine)
    _configure_sqlite(engine)
    _auto_migrate(engine)


def _configure_sqlite(engine) -> None:
    """WAL mode lets the dashboard read while discovery writes in another process."""
    from sqlalchemy import text

    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=30000"))
        conn.commit()


def _auto_migrate(engine) -> None:
    """Add columns that exist in the models but not in the live SQLite file.

    create_all() only creates missing tables, never missing columns, so
    deployments that pull a new version would otherwise crash on new fields.
    SQLite supports ALTER TABLE ... ADD COLUMN, which covers our needs.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}'
            default = getattr(column.default, "arg", None)
            if default is not None and not callable(default):
                if isinstance(default, bool):
                    ddl += f" DEFAULT {int(default)}"
                elif isinstance(default, (int, float)):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, str):
                    ddl += f" DEFAULT '{default}'"
            with engine.begin() as conn:
                conn.execute(text(ddl))


@contextmanager
def session_scope():
    session: Session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI dependency."""
    session: Session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
