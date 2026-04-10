"""
Schema Match Grader — progressive, weighted schema evaluation.

Scoring model (three buckets):
  Table existence   → weight 0.40
  Column correctness → weight 0.40   (set-diff: matched / expected)
  Constraints        → weight 0.20   (CHECK, index, type, nullable)

If a table does not exist:
  - Its table_score bucket contribution = 0.0
  - Its column_score bucket contribution = 0.0  (expected cols all "missing")
  - Constraint checks for it are skipped (score = 0 for those items)

Returns score in [0.0, 1.0].
"""
from __future__ import annotations
import logging
from typing import Any

from sqlalchemy.engine import Engine

from app.graders.base import BaseGrader
from app.inspector import (
    get_table_columns, table_exists,
    check_constraint_exists, index_exists,
    get_schema_snapshot,
)

logger = logging.getLogger(__name__)

WEIGHT_TABLE      = 0.40
WEIGHT_COLUMN     = 0.40
WEIGHT_CONSTRAINT = 0.20


class SchemaGrader(BaseGrader):
    def score(self, engine: Engine, requirements: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        try:
            return self._score(engine, requirements)
        except Exception as e:
            logger.error("SchemaGrader.score() raised unexpectedly: %s", e, exc_info=True)
            return 0.0, {"error": str(e)}

    # ------------------------------------------------------------------
    def _score(self, engine: Engine, requirements: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        snapshot = get_schema_snapshot(engine)
        main_table = requirements.get("table", "")

        # ================================================================
        # BUCKET 1 — Table existence (weight 0.40)
        # ================================================================
        required_tables: list[str] = requirements.get("required_tables", [])
        table_checks: dict[str, bool] = {}
        for tbl in required_tables:
            table_checks[tbl] = table_exists(engine, tbl)

        if table_checks:
            table_score = sum(table_checks.values()) / len(table_checks)
        else:
            table_score = 1.0  # no table requirements → full marks

        # ================================================================
        # BUCKET 2 — Column correctness (weight 0.40)
        # ================================================================
        column_diff: dict[str, dict] = {}

        # 2a. Main-table required columns
        main_col_reqs: list[dict] = requirements.get("required_columns", [])
        if main_table and main_col_reqs:
            expected = {c["name"] for c in main_col_reqs}
            actual   = get_table_columns(engine, main_table)
            matched  = expected & actual
            missing  = expected - actual
            extra    = actual   - expected
            col_score = len(matched) / len(expected) if expected else 1.0
            column_diff[main_table] = {
                "matched": sorted(matched),
                "missing": sorted(missing),
                "extra":   sorted(extra),
                "score":   round(col_score, 4),
            }
            if missing:
                logger.debug("schema_grader: %s missing columns on '%s': %s", len(missing), main_table, missing)

        # 2b. Sub-table required columns (any table-specific block in requirements)
        for key, val in requirements.items():
            if not isinstance(val, dict):
                continue
            sub_col_reqs = val.get("required_columns", [])
            if not sub_col_reqs:
                continue
            sub_table = key
            expected = {c["name"] for c in sub_col_reqs}
            actual   = get_table_columns(engine, sub_table)
            matched  = expected & actual
            missing  = expected - actual
            extra    = actual   - expected
            col_score = len(matched) / len(expected) if expected else 1.0
            column_diff[sub_table] = {
                "matched": sorted(matched),
                "missing": sorted(missing),
                "extra":   sorted(extra),
                "score":   round(col_score, 4),
            }
            if not table_checks.get(sub_table, True):
                logger.warning("Table '%s' does not exist yet — continuing evaluation", sub_table)

        # 2c. products removed-columns check (treated as a column correctness dimension)
        products_reqs  = requirements.get("products", {})
        removed_cols: list[str] = products_reqs.get("removed_columns", [])
        if removed_cols:
            actual_products = get_table_columns(engine, "products")
            removed_ok   = [c for c in removed_cols if c not in actual_products]
            still_present = [c for c in removed_cols if c in actual_products]
            rm_score      = len(removed_ok) / len(removed_cols)
            column_diff["products_removed"] = {
                "removed_ok":    removed_ok,
                "still_present": still_present,
                "score":         round(rm_score, 4),
            }

        if column_diff:
            column_score = sum(r["score"] for r in column_diff.values()) / len(column_diff)
        else:
            column_score = 1.0

        # ================================================================
        # BUCKET 3 — Constraints (weight 0.20)
        # Covers: CHECK constraints, indexes, column type/nullable
        # ================================================================
        constraint_checks: list[bool] = []
        constraint_details: dict[str, Any] = {}

        # 3a. Required CHECK constraints
        missing_constraints: list[str] = []
        for cc in requirements.get("required_check_constraints", []):
            tbl  = requirements.get("table", main_table)
            name = cc["name"]
            exists = check_constraint_exists(engine, tbl, name)
            constraint_checks.append(exists)
            constraint_details[f"check_{tbl}.{name}"] = exists
            if not exists:
                missing_constraints.append(f"CHECK ({name})")

        # 3b. Required indexes
        for idx_req in requirements.get("required_indexes", []):
            idx_table = idx_req.get("table", main_table)
            idx_name  = idx_req["name"]
            exists    = index_exists(engine, idx_table, idx_name)
            constraint_checks.append(exists)
            constraint_details[f"index_{idx_table}.{idx_name}"] = exists
            if not exists:
                missing_constraints.append(f"INDEX ({idx_name})")

        # 3c. Column type / nullable expectations on main table
        actual_main_cols = snapshot.get(main_table, {}).get("columns", [])
        for col_req in main_col_reqs:
            col_name = col_req["name"]

            if "type_contains" in col_req:
                expected_type = col_req["type_contains"].upper()
                actual_type   = next(
                    (c["type"].upper() for c in actual_main_cols if c["name"] == col_name), ""
                )
                type_ok = expected_type in actual_type
                constraint_checks.append(type_ok)
                constraint_details[f"type_{main_table}.{col_name}"] = (
                    f"{actual_type} (expected contains '{expected_type}'): {type_ok}"
                )

            if "nullable" in col_req:
                expected_nullable = col_req["nullable"]
                actual_nullable   = next(
                    (c["nullable"] for c in actual_main_cols if c["name"] == col_name), True
                )
                nullable_ok = actual_nullable == expected_nullable
                constraint_checks.append(nullable_ok)
                constraint_details[f"nullable_{main_table}.{col_name}"] = nullable_ok

        if constraint_checks:
            constraint_score = sum(1 for c in constraint_checks if c) / len(constraint_checks)
        else:
            constraint_score = 1.0  # no constraint requirements → full marks

        # ================================================================
        # Composite schema score
        # ================================================================
        schema_score = (
            WEIGHT_TABLE      * table_score
            + WEIGHT_COLUMN     * column_score
            + WEIGHT_CONSTRAINT * constraint_score
        )

        # ================================================================
        # Human-readable feedback (for demo / logging)
        # ================================================================
        feedback_parts: list[str] = []

        # Report missing tables
        missing_tables = [t for t, ok in table_checks.items() if not ok]
        if missing_tables:
            feedback_parts.append(f"Missing tables: {', '.join(missing_tables)}")

        # Report missing / extra columns per table
        for tbl_name, diff in column_diff.items():
            if diff.get("missing"):
                feedback_parts.append(
                    f"Missing columns on {tbl_name}:\n    - " + "\n    - ".join(sorted(diff['missing']))
                )
            if diff.get("extra"):
                feedback_parts.append(
                    f"Non-target columns present on {tbl_name} (ignored):\n    - " + "\n    - ".join(sorted(diff['extra']))
                )
            if diff.get("still_present"):
                feedback_parts.append(
                    f"Columns still present in source table {tbl_name}:\n    - " + "\n    - ".join(sorted(diff['still_present']))
                )

        # Report missing constraints
        if missing_constraints:
            feedback_parts.append(f"Missing constraints: {'; '.join(missing_constraints)}")

        feedback = "\n".join(feedback_parts) if feedback_parts else "Schema looks correct"

        details: dict[str, Any] = {
            # Top-level scores for transparency
            "table_score":      round(table_score, 4),
            "column_score":     round(column_score, 4),
            "constraint_score": round(constraint_score, 4),
            # Per-table existence map
            "table_exists":     table_checks,
            # Column set-diff per table
            "column_diff":      column_diff,
            # Constraint detail
            "constraint_checks": constraint_details,
            # Human-readable explanation
            "feedback":         feedback,
        }

        logger.debug(
            "SchemaGrader: table=%.3f column=%.3f constraint=%.3f → schema=%.4f | %s",
            table_score, column_score, constraint_score, schema_score, feedback,
        )

        return round(schema_score, 4), details
