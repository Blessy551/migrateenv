"""
Database connection management for MigrateEnv — Supabase compatible.

Supabase free tier requires:
  - SSL (sslmode=require)
  - Session-mode pooler (port 5432) for persistent connections.
    If using transaction-mode (port 6543), use NullPool only.

Connection strategy:
  - Port 5432 (session mode)  → QueuePool(pool_size=2) with pre_ping=True
    This keeps a small pool of warm SSL connections reused across requests,
    eliminating the per-step SSL handshake overhead that caused timeouts.
  - Port 6543 (transaction mode) → NullPool (pgbouncer is stateful, no
    persistent connections allowed).
  - SQLite (local dev)        → default StaticPool / no pool args needed.

Set DATABASE_URL to your Supabase connection string, e.g.:
  Session mode  (port 5432):  postgresql://postgres.[ref]:[pw]@...pooler.supabase.com:5432/postgres
  Transaction mode (port 6543): postgresql://postgres.[ref]:[pw]@...pooler.supabase.com:6543/postgres
"""
import os
import logging
from contextlib import contextmanager
from typing import Generator
from urllib.parse import urlparse

from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool, QueuePool

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is required (Supabase connection string)"
    )

# Normalise postgres:// → postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def _get_port(url: str) -> int:
    """Extract the port from a database URL, default to 5432."""
    try:
        return urlparse(url).port or 5432
    except Exception:
        return 5432


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

    port = _get_port(url)

    if port == 6543:
        # Transaction-mode pooler (pgbouncer) — must use NullPool.
        # pgbouncer does not support persistent connections / prepared statements.
        logger.info("Using NullPool (transaction-mode pooler on port 6543)")
        return create_engine(
            url,
            poolclass=NullPool,
            connect_args=connect_args,
            echo=False,
        )
    else:
        # Session-mode pooler (port 5432) — use a small QueuePool with
        # pre_ping so stale connections are recycled instead of causing errors.
        # pool_size=2 is enough for a single-worker FastAPI server.
        logger.info("Using QueuePool(pool_size=2) with pre_ping (session-mode pooler on port %d)", port)
        return create_engine(
            url,
            poolclass=QueuePool,
            pool_size=2,
            max_overflow=2,
            pool_timeout=30,
            pool_recycle=300,    # recycle connections every 5 min
            pool_pre_ping=True,  # test connection health before use
            connect_args=connect_args,
            echo=False,
        )


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_admin_engine():
    """
    Returns an engine with AUTOCOMMIT isolation for admin operations.
    On Supabase free tier there is no separate admin DB to connect to.
    """
    connect_args = _build_connect_args(DATABASE_URL)
    port = _get_port(DATABASE_URL)

    if port == 6543:
        return create_engine(
            DATABASE_URL,
            poolclass=NullPool,
            connect_args=connect_args,
            isolation_level="AUTOCOMMIT",
            echo=False,
        )
    else:
        return create_engine(
            DATABASE_URL,
            poolclass=QueuePool,
            pool_size=1,
            max_overflow=1,
            pool_pre_ping=True,
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
