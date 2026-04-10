"""
Data Integrity Grader — checks that row counts and data values are preserved.
Returns score in [0.0, 1.0].
"""
from __future__ import annotations
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.graders.base import BaseGrader
from app.inspector import get_row_counts

logger = logging.getLogger(__name__)


class DataGrader(BaseGrader):
    def score(self, engine: Engine, requirements: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        checks = []
        details = {}

        row_counts = get_row_counts(engine)

        # --- Check required row counts ---
        for table, expected_count in requirements.get("required_row_counts", {}).items():
            actual = row_counts.get(table, -1)
            ok = (actual == expected_count)
            checks.append(ok)
            details[f"row_count_{table}"] = {
                "expected": expected_count,
                "actual": actual,
                "ok": ok,
            }

        # --- Check audit_log has minimum rows ---
        audit_min = requirements.get("audit_log_min_rows", 0)
        if audit_min > 0:
            actual_audit = row_counts.get("audit_log", 0)
            ok = actual_audit >= audit_min
            checks.append(ok)
            details["audit_log_min_rows"] = {
                "expected_min": audit_min,
                "actual": actual_audit,
                "ok": ok,
            }

        # --- Check required status values (hard task) ---
        status_req = requirements.get("required_status_values", {})
        if status_req:
            query = status_req.get("query", "")
            must_contain = status_req.get("must_contain", [])
            try:
                with engine.connect() as conn:
                    result = conn.execute(text(query))
                    values = [row[0] for row in result.fetchall()]
                for val in must_contain:
                    has_val = val in values
                    checks.append(has_val)
                    details[f"status_value_{val}"] = has_val
                details["actual_status_values"] = values
            except Exception as e:
                details["status_values_error"] = str(e)
                for val in must_contain:
                    checks.append(False)

        # --- Bug 4: Check product_pricing has at least one row ---
        if "product_pricing" in requirements.get("required_tables", []):
            pp_count = row_counts.get("product_pricing", 0)
            ok = pp_count > 0
            checks.append(ok)
            details["product_pricing_has_rows"] = {"count": pp_count, "ok": ok}

            if ok:
                try:
                    with engine.connect() as conn:
                        orig = conn.execute(text(
                            "SELECT COUNT(*) FROM product_pricing WHERE unit_price IS NOT NULL"
                        )).scalar()
                    ok2 = (orig == pp_count)
                    checks.append(ok2)
                    details["product_pricing_price_populated"] = {"non_null_prices": orig, "ok": ok2}
                except Exception as e:
                    logger.warning("data_grader: product_pricing price check failed: %s", e)
                    details["product_pricing_price_populated"] = {"error": str(e), "skipped": True}
            else:
                details["product_pricing_price_populated"] = {"skipped": True, "reason": "table empty or missing"}

        if not checks:
            return 1.0, {"note": "No data requirements specified"}

        score = sum(1 for c in checks if c) / len(checks)
        details["total_checks"] = len(checks)
        details["passed_checks"] = sum(1 for c in checks if c)
        return round(score, 4), details
