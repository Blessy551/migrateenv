"""
Inspector — takes schema snapshots and row counts from a live DB.
Supports both PostgreSQL (schema="public") and SQLite (schema=None).
Used by graders to compare before/after migration states.
"""
from __future__ import annotations
import logging
from typing import Any

from sqlalchemy import text, inspect as sa_inspect
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _schema(engine: Engine) -> str | None:
    """Return 'public' for PostgreSQL, None for SQLite."""
    dialect = engine.dialect.name
    if dialect == "sqlite":
        return None
    return "public"


def get_schema_snapshot(engine: Engine) -> dict[str, Any]:
    """
    Returns a full schema snapshot:
    {
      "table_name": {
        "columns": [{"name": str, "type": str, "nullable": bool, "default": str|None}],
        "primary_keys": [str],
        "foreign_keys": [{"constrained_columns": [...], "referred_table": str, "referred_columns": [...]}],
        "indexes": [{"name": str, "columns": [...], "unique": bool}],
        "check_constraints": [{"name": str, "sqltext": str}],
        "unique_constraints": [{"name": str, "columns": [...]}],
      }
    }
    """
    schema = _schema(engine)
    inspector = sa_inspect(engine)
    tables = inspector.get_table_names(schema=schema)
    snapshot = {}

    for table in sorted(tables):
        columns = []
        for col in inspector.get_columns(table, schema=schema):
            columns.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "default": str(col.get("default", "")) if col.get("default") is not None else None,
            })

        pks = inspector.get_pk_constraint(table, schema=schema)
        fks = []
        for fk in inspector.get_foreign_keys(table, schema=schema):
            fks.append({
                "constrained_columns": fk["constrained_columns"],
                "referred_table": fk["referred_table"],
                "referred_columns": fk["referred_columns"],
            })

        indexes = []
        for idx in inspector.get_indexes(table, schema=schema):
            indexes.append({
                "name": idx["name"],
                "columns": idx["column_names"],
                "unique": idx.get("unique", False),
            })

        check_constraints = []
        try:
            for cc in inspector.get_check_constraints(table, schema=schema):
                check_constraints.append({
                    "name": cc.get("name", ""),
                    "sqltext": cc.get("sqltext", ""),
                })
        except Exception:
            pass

        unique_constraints = []
        try:
            for uc in inspector.get_unique_constraints(table, schema=schema):
                unique_constraints.append({
                    "name": uc.get("name", ""),
                    "columns": uc.get("column_names", []),
                })
        except Exception:
            pass

        snapshot[table] = {
            "columns": columns,
            "primary_keys": pks.get("constrained_columns", []),
            "foreign_keys": fks,
            "indexes": indexes,
            "check_constraints": check_constraints,
            "unique_constraints": unique_constraints,
        }

    return snapshot


def get_row_counts(engine: Engine) -> dict[str, int]:
    """Returns {table_name: row_count} for all tables."""
    schema = _schema(engine)
    inspector = sa_inspect(engine)
    tables = inspector.get_table_names(schema=schema)
    counts = {}
    with engine.connect() as conn:
        for table in tables:
            try:
                result = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))
                counts[table] = result.scalar()
            except Exception as e:
                logger.warning(f"Could not count rows in {table}: {e}")
                counts[table] = -1
    return counts


def get_foreign_keys(engine: Engine) -> list[dict]:
    """Returns all FK relationships across all tables."""
    schema = _schema(engine)
    inspector = sa_inspect(engine)
    all_fks = []
    for table in inspector.get_table_names(schema=schema):
        for fk in inspector.get_foreign_keys(table, schema=schema):
            all_fks.append({
                "from_table": table,
                "from_columns": fk["constrained_columns"],
                "to_table": fk["referred_table"],
                "to_columns": fk["referred_columns"],
            })
    return all_fks


def get_table_columns(engine: Engine, table: str) -> set[str]:
    """
    Returns the set of column names for *table*.
    Returns an empty set (never raises) if the table does not exist.
    """
    schema = _schema(engine)
    inspector = sa_inspect(engine)
    try:
        return {c["name"] for c in inspector.get_columns(table, schema=schema)}
    except NoSuchTableError:
        logger.warning("get_table_columns: table '%s' does not exist — returning empty set", table)
        return set()
    except Exception as e:
        logger.warning("get_table_columns: error inspecting '%s': %s", table, e)
        return set()


def column_exists(engine: Engine, table: str, column: str) -> bool:
    schema = _schema(engine)
    inspector = sa_inspect(engine)
    try:
        cols = [c["name"] for c in inspector.get_columns(table, schema=schema)]
        return column in cols
    except NoSuchTableError:
        logger.warning("column_exists: table '%s' does not exist — returning False", table)
        return False
    except Exception as e:
        logger.warning("column_exists: unexpected error inspecting '%s.%s': %s", table, column, e)
        return False


def table_exists(engine: Engine, table: str) -> bool:
    schema = _schema(engine)
    inspector = sa_inspect(engine)
    return table in inspector.get_table_names(schema=schema)


def index_exists(engine: Engine, table: str, index_name: str) -> bool:
    schema = _schema(engine)
    inspector = sa_inspect(engine)
    try:
        return any(
            idx["name"].lower() == index_name.lower()
            for idx in inspector.get_indexes(table, schema=schema)
        )
    except NoSuchTableError:
        logger.warning("index_exists: table '%s' does not exist — returning False", table)
        return False
    except Exception as e:
        logger.warning("index_exists: unexpected error inspecting '%s' for index '%s': %s", table, index_name, e)
        return False


def check_constraint_exists(engine: Engine, table: str, constraint_name: str) -> bool:
    schema = _schema(engine)
    inspector = sa_inspect(engine)
    try:
        constraints = inspector.get_check_constraints(table, schema=schema)
        return any(c.get("name", "").lower() == constraint_name.lower() for c in constraints)
    except Exception:
        return False
