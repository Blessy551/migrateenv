"""
Database connection management for MigrateEnv — Supabase compatible.

Supabase free tier requires:
  - SSL (sslmode=require)
  - No DROP/CREATE DATABASE
  - Session-mode pooler (port 5432) OR transaction-mode pooler (port 6543)
    Use session mode (5432) if your DATABASE_URL uses the pooler hostname.

Set DATABASE_URL to your Supabase connection string, e.g.:
  postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres
"""
import os
import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is required (Supabase connection string)"
    )

# Normalise postgres:// → postgresql:// (some Supabase dashboards emit the old scheme)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def _build_connect_args(url: str) -> dict:
    """Return psycopg2 connect_args with SSL for non-SQLite URLs."""
    if url.startswith("sqlite"):
        return {}
    return {
        "sslmode": "require",
        "connect_timeout": 30,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }


def get_engine(url: str = DATABASE_URL):
    connect_args = _build_connect_args(url)

    if url.startswith("sqlite"):
        return create_engine(url, echo=False)

    return create_engine(
        url,
        # NullPool: one connection per request — safest for Supabase pooler
        # (avoids "prepared statement already exists" errors in pgbouncer
        #  transaction mode).  Switch to QueuePool only if using session mode.
        poolclass=NullPool,
        connect_args=connect_args,
        echo=False,
    )


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_admin_engine():
    """
    On Supabase free tier there is no separate admin DB to connect to.
    Returns the same engine with AUTOCOMMIT (harmless for our use-case
    because initialize_db no longer needs DROP/CREATE DATABASE).
    """
    connect_args = _build_connect_args(DATABASE_URL)
    return create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        connect_args=connect_args,
        isolation_level="AUTOCOMMIT",
        echo=False,
    )


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency for injecting DB sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping_db() -> bool:
    """Returns True if the database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("ping_db failed: %s", e)
        return False


def reconnect(url: str = DATABASE_URL):
    """Rebuild the global engine (called after DB reset)."""
    global engine, SessionLocal
    try:
        engine.dispose()
    except Exception:
        pass
    engine = get_engine(url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
