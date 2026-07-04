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

    Base.metadata.create_all(get_engine())


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
