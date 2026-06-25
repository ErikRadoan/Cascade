"""Database engine, declarative base, and session factory.

SQLAlchemy 2.0 sync setup using SQLite by default.
Switch to Postgres by setting CASCADE_DATABASE_URL environment variable:

    CASCADE_DATABASE_URL=postgresql+psycopg2://user:pass@localhost/cascade

The session factory is used as a FastAPI dependency:

    @router.get("/jobs")
    def list_jobs(db: Session = Depends(get_db)):
        return JobRepository(db).list()

On first import, create_all() is called so tables are created automatically.
In production, replace with Alembic migrations.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------------------
# Database URL
# ---------------------------------------------------------------------------
# Default: SQLite file next to the cascade home directory.
# Override with CASCADE_DATABASE_URL env var for Postgres or other backends.

_DEFAULT_DB_PATH = Path.home() / ".cascade" / "cascade.db"
_DEFAULT_URL = f"sqlite:///{_DEFAULT_DB_PATH}"

DATABASE_URL = os.getenv("CASCADE_DATABASE_URL", _DEFAULT_URL)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
# connect_args only needed for SQLite — disables the same-thread check so
# the session can be used across FastAPI's thread pool.

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

is_debug = os.environ.get('DEBUG', 'False').lower() == 'true'

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    echo=is_debug,       # set True to log all SQL — useful for debugging
)

# Enable WAL mode for SQLite so readers don't block writers and concurrent
# requests see committed writes immediately instead of a stale snapshot.
if DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_wal(dbapi_conn, _connection_record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA synchronous=NORMAL")

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

# ---------------------------------------------------------------------------
# Declarative base — all ORM models inherit from this
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass

# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

def get_db():
    """Yield a database session, closing it after the request completes.

    Usage:
        from ..repositories.db import get_db
        from sqlalchemy.orm import Session

        @router.get("/")
        def endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()   # flush any uncommitted work at the end of the request
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

def create_tables() -> None:
    """Create all tables defined in ORM models.

    Called once at application startup from main.py.
    Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS internally.
    """
    # Import models here so Base.metadata knows about them before create_all
    from . import models  # noqa: F401
    _DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)