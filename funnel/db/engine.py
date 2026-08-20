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

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _ensure_columns(engine)


def _ensure_columns(engine) -> None:
    """Additive micro-migrations for pre-existing databases (create_all never
    ALTERs). Each entry: table -> {column: DDL}."""
    from sqlalchemy import text

    additions = {
        "companynode": {"financials": "JSON DEFAULT '{}'"},
        # scraped | model_knowledge | isr | "" (pre-migration rows / manual)
        "evidenceitem": {"provenance": "TEXT DEFAULT ''"},
        "researchrun": {"usage": "JSON DEFAULT '{}'"},
    }
    with engine.connect() as conn:
        for table, cols in additions.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if not existing:  # table not created yet (fresh DB) — create_all handled it
                continue
            for col, ddl in cols.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        conn.commit()


def new_session() -> Session:
    # expire_on_commit=False keeps attributes readable after the session closes,
    # so repo functions can safely return detached objects.
    return Session(get_engine(), expire_on_commit=False)
