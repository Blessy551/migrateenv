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

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool, QueuePool

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# FIX: Read DATABASE_URL lazily — do NOT raise at import time.
# The validator container imports this module before env vars are injected.
# ---------------------------------------------------------------------------
DATABASE_URL: str | None = os.getenv("DATABASE_URL")

# Normalise postgres:// → postgresql:// if present
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


def _require_database_url() -> str:
    """Return DATABASE_URL, raising a clear error only when actually needed."""
    url = os.getenv("DATABASE_URL") or DATABASE_URL
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is required (Supabase connection string)"
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


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
    hostname = (urlparse(url).hostname or "").lower()
    is_local = hostname in {"localhost", "127.0.0.1", "host.docker.internal"}
    return {
        "connect_timeout": 30,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
        **({} if is_local else {"sslmode": "require"}),
    }


def get_engine(url: str | None = None):
    """Build and return a SQLAlchemy engine for the given URL."""
    if url is None:
        url = _require_database_url()

    connect_args = _build_connect_args(url)

    if url.startswith("sqlite"):
        return create_engine(url, echo=False)

    port = _get_port(url)

    if port == 6543:
        # Transaction-mode pooler (pgbouncer) — must use NullPool.
        logger.info("Using NullPool (transaction-mode pooler on port 6543)")
        return create_engine(
            url,
            poolclass=NullPool,
            connect_args=connect_args,
            echo=False,
        )
    else:
        # Session-mode pooler (port 5432) — small QueuePool with pre_ping.
        logger.info(
            "Using QueuePool(pool_size=2) with pre_ping (session-mode pooler on port %d)",
            port,
        )
        return create_engine(
            url,
            poolclass=QueuePool,
            pool_size=2,
            max_overflow=2,
            pool_timeout=30,
            pool_recycle=300,
            pool_pre_ping=True,
            connect_args=connect_args,
            echo=False,
        )


# ---------------------------------------------------------------------------
# FIX: Lazy global engine — built on first use, not at import time.
# ---------------------------------------------------------------------------
_engine = None
_SessionLocal = None


def _get_global_engine():
    """Return (and lazily create) the process-wide engine."""
    global _engine, _SessionLocal
    if _engine is None:
        _engine = get_engine(_require_database_url())
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def _get_session_factory():
    _get_global_engine()  # ensure _SessionLocal is initialised
    return _SessionLocal


# Keep module-level names that other modules reference, resolved lazily via properties.
# Modules that do `from app.db.connection import engine` get the lazy accessor instead.
class _LazyEngine:
    """Proxy that forwards attribute access to the real engine on first use."""

    def __getattr__(self, name):
        return getattr(_get_global_engine(), name)

    def connect(self):
        return _get_global_engine().connect()

    def dispose(self):
        if _engine is not None:
            _engine.dispose()

    def begin(self):
        return _get_global_engine().begin()


engine = _LazyEngine()
SessionLocal = None  # kept for backwards compat — use get_session() instead


# ---------------------------------------------------------------------------
# Admin engine (AUTOCOMMIT isolation level)
# ---------------------------------------------------------------------------

def get_admin_engine():
    """
    Returns an engine with AUTOCOMMIT isolation for admin operations.
    On Supabase free tier there is no separate admin DB to connect to.
    """
    url = _require_database_url()
    connect_args = _build_connect_args(url)
    port = _get_port(url)

    if port == 6543:
        return create_engine(
            url,
            poolclass=NullPool,
            connect_args=connect_args,
            isolation_level="AUTOCOMMIT",
            echo=False,
        )
    else:
        return create_engine(
            url,
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
    session = _get_session_factory()()
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
    db = _get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def ping_db() -> bool:
    """Returns True if the database is reachable."""
    try:
        with _get_global_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("ping_db failed: %s", e)
        return False


def reconnect(url: str | None = None):
    """Rebuild the global engine (called after DB reset)."""
    global _engine, _SessionLocal
    if url is None:
        url = _require_database_url()
    try:
        if _engine is not None:
            _engine.dispose()
    except Exception:
        pass
    _engine = get_engine(url)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)