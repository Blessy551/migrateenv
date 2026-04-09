"""
Database loader — resets the Northwind schema by running the SQL dump
directly against the existing database (no DROP/CREATE DATABASE).

Supabase free tier does NOT allow DROP DATABASE or CREATE DATABASE via
the pooler. The northwind.sql file already contains DROP TABLE IF EXISTS
statements for every Northwind table, so we just need to:

  1. Drop task-created tables (product_pricing, audit_log, etc.)
  2. Execute northwind.sql (which drops + recreates all Northwind tables)
"""
import os
import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SQL_DUMP_PATH = Path(__file__).parent.parent.parent / "db" / "northwind.sql"

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is required (Supabase connection string)"
    )

# Extra tables created by tasks that must be cleaned up on reset
_TASK_EXTRA_TABLES = [
    "product_pricing",
    "audit_log",
]


def _parse_dsn(url: str) -> dict:
    """Parse postgresql://user:pass@host:port/dbname into components."""
    p = urlparse(url)
    return {
        "host": p.hostname or "localhost",
        "port": p.port or 5432,
        "user": p.username or "postgres",
        "password": p.password or "postgres",
        "dbname": p.path.lstrip("/") or "postgres",
    }


def _connect(dsn: dict):
    """Open a psycopg2 connection with SSL (required by Supabase)."""
    import psycopg2
    return psycopg2.connect(
        host=dsn["host"],
        port=dsn["port"],
        user=dsn["user"],
        password=dsn["password"],
        dbname=dsn["dbname"],
        sslmode="require",
        connect_timeout=30,
    )


def initialize_db(
    sql_path: Path = SQL_DUMP_PATH,
    database_url: str = DATABASE_URL,
) -> None:
    """
    Reset the database to a clean Northwind state.

    Works on Supabase free tier (no superuser / DROP DATABASE needed):
      - Drops task-created tables first (CASCADE to remove FKs)
      - Executes the northwind.sql dump which already contains
        DROP TABLE IF EXISTS for all Northwind tables
    """
    dsn = _parse_dsn(database_url)

    # Step 1 — drop task-specific tables not in northwind.sql
    conn = _connect(dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for table in _TASK_EXTRA_TABLES:
                try:
                    cur.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
                    logger.info("Dropped task table: %s", table)
                except Exception as drop_err:
                    logger.warning("Could not drop %s: %s", table, drop_err)
    finally:
        conn.close()

    # Step 2 — run the full northwind.sql dump
    if not sql_path.exists():
        raise FileNotFoundError(f"Northwind SQL dump not found at: {sql_path}")

    with open(sql_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    conn = _connect(dsn)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(sql_content)
        conn.commit()
        logger.info("Northwind SQL dump executed and committed.")
    except Exception as e:
        conn.rollback()
        logger.error("Failed to load SQL dump: %s", e)
        raise
    finally:
        conn.close()


def get_table_row_counts(database_url: str = DATABASE_URL) -> dict:
    dsn = _parse_dsn(database_url)
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
            tables = [row[0] for row in cur.fetchall()]
            counts = {}
            for table in tables:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                counts[table] = cur.fetchone()[0]
        return counts
    finally:
        conn.close()
