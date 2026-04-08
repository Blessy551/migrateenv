"""
Database loader — drops and recreates the Northwind schema from the SQL dump.
Supports both PostgreSQL (full psycopg2 path) and SQLite (HF Spaces fallback).
"""
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SQL_DUMP_PATH = Path(__file__).parent.parent.parent / "db" / "northwind.sql"

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./northwind.db"
)


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _parse_dsn(url: str) -> dict:
    """Parse postgresql://user:pass@host:port/dbname into components."""
    from urllib.parse import urlparse

    p = urlparse(url)
    return {
        "host": p.hostname or "localhost",
        "port": p.port or 5432,
        "user": p.username or "postgres",
        "password": p.password or "postgres",
        "dbname": p.path.lstrip("/") or "northwind",
    }


def _initialize_sqlite(sql_path: Path, database_url: str) -> None:
    import shutil
    from urllib.parse import urlparse

    db_file = urlparse(database_url).path.lstrip("/")
    source_db = Path(__file__).parent / "northwind.db"

    if not source_db.exists():
        raise FileNotFoundError(
            f"northwind.db not found at {source_db}. Please add it."
        )

    if os.path.exists(db_file):
        os.remove(db_file)

    shutil.copy(source_db, db_file)

    logger.info("SQLite DB copied successfully (prebuilt)")


def _get_admin_conn(dsn: dict):
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    conn = psycopg2.connect(
        host=dsn["host"],
        port=dsn["port"],
        user=dsn["user"],
        password=dsn["password"],
        dbname="postgres",
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def _load_sql_file_pg(sql_path: Path, dsn: dict) -> None:
    import psycopg2

    conn = psycopg2.connect(
        host=dsn["host"],
        port=dsn["port"],
        user=dsn["user"],
        password=dsn["password"],
        dbname=dsn["dbname"],
    )
    conn.autocommit = False

    try:
        with open(sql_path, "r", encoding="utf-8") as f:
            sql_content = f.read()

        with conn.cursor() as cur:
            cur.execute(sql_content)

        conn.commit()
        logger.info("SQL dump executed and committed.")

    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to load SQL dump: {e}")
        raise

    finally:
        conn.close()


def initialize_db(sql_path: Path = SQL_DUMP_PATH, database_url: str = DATABASE_URL) -> None:
    if _is_sqlite(database_url):
        _initialize_sqlite(sql_path, database_url)
        return

    import psycopg2
    from psycopg2 import sql as pg_sql

    dsn = _parse_dsn(database_url)
    db_name = dsn["dbname"]

    admin_conn = _get_admin_conn(dsn)

    try:
        cur = admin_conn.cursor()

        cur.execute(
            pg_sql.SQL(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()"
            ),
            [db_name],
        )

        cur.execute(pg_sql.SQL("DROP DATABASE IF EXISTS {}").format(pg_sql.Identifier(db_name)))
        cur.execute(pg_sql.SQL("CREATE DATABASE {}").format(pg_sql.Identifier(db_name)))

        cur.close()

    finally:
        admin_conn.close()

    if not sql_path.exists():
        raise FileNotFoundError(f"Northwind SQL dump not found at: {sql_path}")

    _load_sql_file_pg(sql_path, dsn)


def get_table_row_counts(database_url: str = DATABASE_URL) -> dict:
    if _is_sqlite(database_url):
        import sqlite3
        from urllib.parse import urlparse

        db_file = urlparse(database_url).path.lstrip("/")
        conn = sqlite3.connect(db_file)

        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in cur.fetchall()]

            counts = {}
            for table in tables:
                row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                counts[table] = row[0]

            return counts

        finally:
            conn.close()

    import psycopg2
    dsn = _parse_dsn(database_url)
    conn = psycopg2.connect(**dsn)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename
                """
            )

            tables = [row[0] for row in cur.fetchall()]
            counts = {}

            for table in tables:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                counts[table] = cur.fetchone()[0]

        return counts

    finally:
        conn.close()