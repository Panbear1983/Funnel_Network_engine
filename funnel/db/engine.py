"""SQLite engine + session management."""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from funnel import config

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        config.ensure_dirs()
        # check_same_thread=False so the research worker thread can use the DB too;
        # sessions are short-lived per operation, so cross-thread sharing is avoided.
        _engine = create_engine(
            f"sqlite:///{config.DB_PATH}", connect_args={"check_same_thread": False}
        )
    return _engine


def init_db() -> None:
    # Importing models registers them on SQLModel.metadata before create_all.
    from funnel.db import models  # noqa: F401

    SQLModel.metadata.create_all(get_engine())


def new_session() -> Session:
    # expire_on_commit=False keeps attributes readable after the session closes,
    # so repo functions can safely return detached objects.
    return Session(get_engine(), expire_on_commit=False)
