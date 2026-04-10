"""
FK / Relational Integrity Grader — verifies all FK constraints are intact,
no orphan rows exist, and required FK relationships are present.
Returns score in [0.0, 1.0].
"""
from __future__ import annotations
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.graders.base import BaseGrader
from app.inspector import get_foreign_keys

logger = logging.getLogger(__name__)


class FKGrader(BaseGrader):
    def score(self, engine: Engine, requirements: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        checks = []
        details = {}

        # --- Check required FKs exist in schema ---
        all_fks = get_foreign_keys(engine)

        pp_reqs = requirements.get("product_pricing", {})
        required_fks = pp_reqs.get("required_foreign_keys", requirements.get("required_foreign_keys", []))
        for fk_req in required_fks:
            from_table = fk_req.get("from_table", requirements.get("table", "product_pricing"))
            from_cols  = fk_req["constrained_columns"]
            to_table   = fk_req["referred_table"]
            to_cols    = fk_req["referred_columns"]

            found = any(
                fk["from_table"] == from_table
                and fk["from_columns"] == from_cols
                and fk["to_table"] == to_table
                for fk in all_fks
            )
            checks.append(found)
            details[f"fk_{from_table}_to_{to_table}"] = found

        # --- Verify no orphan rows for key Northwind FKs ---
        orphan_checks = [
            # orders.customer_id → customers
            ("orders", "customer_id", "customers", "customer_id"),
            # orders.employee_id → employees
            ("orders", "employee_id", "employees", "employee_id"),
            # order_details.order_id → orders
            ("order_details", "order_id", "orders", "order_id"),
            # order_details.product_id → products (medium: also check product_pricing)
            ("order_details", "product_id", "products", "product_id"),
        ]

        for child_table, child_col, parent_table, parent_col in orphan_checks:
            # Only check if both tables exist
            try:
                with engine.connect() as conn:
                    conn.execute(text(f'SELECT 1 FROM "{child_table}" LIMIT 1'))
                    conn.execute(text(f'SELECT 1 FROM "{parent_table}" LIMIT 1'))
            except Exception:
                continue  # table doesn't exist, skip

            try:
                query = (
                    f'SELECT COUNT(*) FROM "{child_table}" c '
                    f'LEFT JOIN "{parent_table}" p ON c."{child_col}" = p."{parent_col}" '
                    f'WHERE p."{parent_col}" IS NULL AND c."{child_col}" IS NOT NULL'
                )
                with engine.connect() as conn:
                    result = conn.execute(text(query))
                    orphan_count = result.scalar()
                no_orphans = (orphan_count == 0)
                checks.append(no_orphans)
                details[f"orphans_{child_table}.{child_col}"] = {
                    "orphan_count": orphan_count,
                    "ok": no_orphans,
                }
            except Exception as e:
                logger.warning(f"Orphan check failed for {child_table}.{child_col}: {e}")
                details[f"orphans_{child_table}.{child_col}"] = {"error": str(e)}

        # --- For medium task: check product_pricing orphans ---
        if any(fk["from_table"] == "product_pricing" for fk in all_fks):
            try:
                query = (
                    'SELECT COUNT(*) FROM product_pricing pp '
                    'LEFT JOIN products p ON pp.product_id = p.product_id '
                    'WHERE p.product_id IS NULL'
                )
                with engine.connect() as conn:
                    result = conn.execute(text(query))
                    orphan_count = result.scalar()
                no_orphans = (orphan_count == 0)
                checks.append(no_orphans)
                details["orphans_product_pricing"] = {"orphan_count": orphan_count, "ok": no_orphans}
            except Exception as e:
                details["orphans_product_pricing_error"] = str(e)

        if not checks:
            return 1.0, {"note": "No FK requirements specified; full marks by default"}

        score = sum(1 for c in checks if c) / len(checks)
        details["total_checks"] = len(checks)
        details["passed_checks"] = sum(1 for c in checks if c)
        return round(score, 4), details
