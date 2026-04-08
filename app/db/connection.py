"""
Database connection management for MigrateEnv.
Supports SQLite (default / HF Spaces) and PostgreSQL (via DATABASE_URL override).
"""
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool, StaticPool

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./northwind.db"
)

ADMIN_URL = os.getenv(
    "ADMIN_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/postgres"
)

# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------
def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def get_engine(url: str = DATABASE_URL):
    if _is_sqlite(url):
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )
    return create_engine(
        url,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_pre_ping=True,
        echo=False,
    )


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_admin_engine():
    """Engine connected to the postgres admin DB for DROP/CREATE DATABASE ops.
    Only meaningful for PostgreSQL deployments."""
    return create_engine(
        ADMIN_URL,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
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
    except Exception:
        return False


def reconnect(url: str = DATABASE_URL):
    """Rebuild the global engine (called after DB reset)."""
    global engine, SessionLocal
    engine.dispose()
    engine = get_engine(url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
