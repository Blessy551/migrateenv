"""
Database loader — resets the Northwind schema by running the SQL dump
directly against the existing database (no DROP/CREATE DATABASE).

Uses SQLAlchemy engine (not raw psycopg2) so it is compatible with
both Supabase session-mode (port 5432) and transaction-mode (port 6543)
poolers, as well as local SQLite for development.

The northwind.sql file contains DROP TABLE IF EXISTS statements for every
Northwind table, so we just need to:
  1. Drop task-created tables (product_pricing, audit_log, etc.)
  2. Execute northwind.sql (which drops + recreates all Northwind tables)
"""
import os
import re
import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

SQL_DUMP_PATH = Path(__file__).parent.parent.parent / "db" / "northwind.sql"

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is required (Supabase connection string)"
    )

# Normalise postgres:// → postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Extra tables created by tasks that must be cleaned up on reset
_TASK_EXTRA_TABLES = [
    "product_pricing",
    "audit_log",
]


def _get_loader_engine(database_url: str):
    """
    Create a SQLAlchemy engine suitable for bulk loading.

    Uses NullPool so it works with both session-mode (5432) and
    transaction-mode (6543) Supabase poolers, as well as local SQLite.
    """
    if database_url.startswith("sqlite"):
        return create_engine(database_url, echo=False)

    return create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={
            "sslmode": "require",
            "connect_timeout": 30,
        },
        echo=False,
    )


def _split_sql_statements(sql_content: str) -> list[str]:
    """
    Split a SQL dump into individual statements, skipping blanks and comments.
    Handles semicolons inside string literals by using a simple state machine.
    """
    statements = []
    current: list[str] = []
    in_string = False
    string_char = ""
    i = 0
    text_len = len(sql_content)

    while i < text_len:
        ch = sql_content[i]

        # Track string literals
        if not in_string and ch in ("'", '"'):
            in_string = True
            string_char = ch
        elif in_string and ch == string_char:
            # Check for escaped quote (doubled)
            if i + 1 < text_len and sql_content[i + 1] == string_char:
                current.append(ch)
                i += 1
            else:
                in_string = False
        elif not in_string and ch == '-' and i + 1 < text_len and sql_content[i + 1] == '-':
            # Single-line comment — skip to end of line
            while i < text_len and sql_content[i] != '\n':
                i += 1
            continue
        elif not in_string and ch == ';':
            stmt = ''.join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    # Last statement (no trailing semicolon)
    leftover = ''.join(current).strip()
    if leftover:
        statements.append(leftover)

    return statements


def initialize_db(
    sql_path: Path = SQL_DUMP_PATH,
    database_url: str = DATABASE_URL,
) -> None:
    """
    Reset the database to a clean Northwind state.

    Uses SQLAlchemy so it works with both Supabase pooler modes and
    local SQLite — no raw psycopg2 needed.
    """
    engine = _get_loader_engine(database_url)
    is_sqlite = database_url.startswith("sqlite")

    # Step 1 — drop task-specific tables not in northwind.sql
    with engine.begin() as conn:
        for table in _TASK_EXTRA_TABLES:
            try:
                conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
                logger.info("Dropped task table: %s", table)
            except Exception as drop_err:
                logger.warning("Could not drop %s: %s", table, drop_err)

    # Step 2 — run the full northwind.sql dump
    if not sql_path.exists():
        raise FileNotFoundError(f"Northwind SQL dump not found at: {sql_path}")

    with open(sql_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    with engine.begin() as conn:
        if is_sqlite:
            statements = _split_sql_statements(sql_content)
            logger.info("Executing %d SQL statements from northwind.sql (SQLite)", len(statements))
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                except Exception as e:
                    logger.warning("Statement failed (continuing): %s | %.120s", e, stmt)
        else:
            logger.info("Executing bulk SQL dump (PostgreSQL)")
            try:
                # PostgreSQL natively handles multi-statement strings efficiently
                conn.execute(text(sql_content))
            except Exception as e:
                logger.error("Bulk SQL dump failed: %s", e)
                raise

    logger.info("Northwind SQL dump executed successfully.")
    engine.dispose()


def get_table_row_counts(database_url: str = DATABASE_URL) -> dict:
    engine = _get_loader_engine(database_url)
    is_postgres = not database_url.startswith("sqlite")
    counts = {}
    try:
        with engine.connect() as conn:
            if is_postgres:
                result = conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename"
                    )
                )
                tables = [row[0] for row in result.fetchall()]
            else:
                result = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
                tables = [row[0] for row in result.fetchall()]

            for table in tables:
                try:
                    r = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))
                    counts[table] = r.scalar()
                except Exception:
                    counts[table] = -1
    finally:
        engine.dispose()
    return counts
