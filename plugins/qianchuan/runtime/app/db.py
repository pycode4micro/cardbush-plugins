from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def build_engine(settings: Settings | None = None) -> Engine:
    runtime_settings = settings or get_settings()
    _ensure_sqlite_parent(runtime_settings.database_url)
    connect_args = (
        {"check_same_thread": False}
        if runtime_settings.database_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(
        runtime_settings.database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        future=True,
    )
    if runtime_settings.database_url.startswith("sqlite"):
        _configure_sqlite(engine)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    engine = build_engine()
    session_factory = create_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return
    raw_path = database_url.removeprefix("sqlite:///")
    if raw_path.startswith(":memory:"):
        return
    Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
