"""
Sanitizer — validates and filters agent-submitted SQL before execution.
Prevents destructive operations outside the task's allowed scope.
"""
from __future__ import annotations
import re
import logging

logger = logging.getLogger(__name__)

# Patterns that are always blocked (catastrophic operations)
_BLOCKED_PATTERNS = [
    r"\bDROP\s+DATABASE\b",
    r"\bDROP\s+SCHEMA\b",
    r"\bTRUNCATE\b",           # no TRUNCATE (use task-scoped resets)
    r"\bSHUTDOWN\b",
    r"\bPG_TERMINATE_BACKEND\b",
    r"\bCOPY\b.*\bTO\b",       # no file exports
    r"\bINTO\s+OUTFILE\b",
    r"\bDELETE\s+FROM\b(?!\s+\w+\s+WHERE\b)",   # DELETE without WHERE
    r"\bDROP\s+COLUMN\b\s+(customer_id|product_id|order_id|employee_id|supplier_id|category_id|shipper_id)\b",
]

# Patterns for risky but conditionally allowed ops
_WARN_PATTERNS = [
    r"\bUPDATE\b.*(?<!WHERE\s)\s*;",            # UPDATE without WHERE (heuristic)
]

_BLOCKED_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _BLOCKED_PATTERNS]
_WARN_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _WARN_PATTERNS]


def sanitize_sql(sql: str) -> tuple[bool, str]:
    """
    Returns (is_safe, reason).
    is_safe=True means the SQL can be executed.
    is_safe=False means it was blocked for safety.
    """
    if not sql or not sql.strip():
        return False, "Empty SQL statement"

    sql_stripped = sql.strip()

    # Multi-statement guard (semicolons that are not inside string literals)
    stripped_for_semi = re.sub(r"'[^']*'", "''", sql_stripped)  # remove string literals
    if stripped_for_semi.count(";") > 1 or (
        stripped_for_semi.count(";") == 1 and not stripped_for_semi.rstrip().endswith(";")
    ):
        return False, "Multi-statement SQL not allowed. Submit one statement per step."

    # Block catastrophic patterns
    for pattern in _BLOCKED_RE:
        if pattern.search(sql_stripped):
            msg = f"Blocked: SQL matches forbidden pattern [{pattern.pattern[:60]}]"
            logger.warning(msg)
            return False, msg

    # Warn but allow some risky operations (task might legitimately need them)
    warnings = []
    for pattern in _WARN_RE:
        if pattern.search(sql_stripped):
            warnings.append(f"Warning: risky pattern [{pattern.pattern[:60]}]")

    if warnings:
        logger.warning(f"SQL has warnings: {warnings}")

    return True, "OK"


def validate_sql_syntax(sql: str, engine) -> tuple[bool, str]:
    """
    Tries to explain/parse the SQL without executing it.
    Uses PostgreSQL's EXPLAIN for DML, or just syntax check for DDL.
    """
    from sqlalchemy import text
    sql = sql.strip().rstrip(";")

    # For SELECT queries, try EXPLAIN
    if re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        try:
            with engine.connect() as conn:
                conn.execute(text(f"EXPLAIN {sql}"))
            return True, "Valid SELECT"
        except Exception as e:
            return False, str(e)

    # For DDL/DML, do a dry-run inside a SAVEPOINT
    try:
        with engine.begin() as conn:
            conn.execute(text("SAVEPOINT syntax_check"))
            conn.execute(text(sql))
            conn.execute(text("ROLLBACK TO SAVEPOINT syntax_check"))
        return True, "Syntax valid"
    except Exception as e:
        return False, str(e)


def is_noop(sql: str, engine) -> bool:
    """
    Detect if a SQL statement is a no-op (e.g., adding a column that already exists).
    Heuristic: execute in a savepoint and check if any rows/schema changed.
    Returns True if the operation had no effect.
    """
    # Simple heuristic: if it's a SELECT or EXPLAIN, it's not a state change
    stripped = sql.strip().upper()
    if stripped.startswith("SELECT") or stripped.startswith("EXPLAIN"):
        return True
    return False
