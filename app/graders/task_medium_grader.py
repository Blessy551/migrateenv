"""
Task 2 Grader (Medium): Split orders table into orders + shipments.

PRD Grading (0.25 each):
- orders schema matches (no address/city/postal columns)
- shipments schema + FK correct
- 200 rows in orders
- 200 rows in shipments with correct order_id
"""
from __future__ import annotations
import logging
from typing import Any
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

class TaskMediumGrader:
    def grade(self, engine: Engine, requirements: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        try:
            orders_schema_score = self._check_orders_schema(engine)
            shipments_schema_score = self._check_shipments_schema(engine)
            orders_data_score = self._check_row_count(engine, "orders", 200)
            shipments_data_score = self._check_row_count(engine, "shipments", 200)
            
            composite = (orders_schema_score * 0.25 + 
                         shipments_schema_score * 0.25 + 
                         orders_data_score * 0.25 + 
                         shipments_data_score * 0.25)
            
            details = {
                "orders_schema_score": round(orders_schema_score, 4),
                "shipments_schema_score": round(shipments_schema_score, 4),
                "orders_data_score": round(orders_data_score, 4),
                "shipments_data_score": round(shipments_data_score, 4),
                "composite_score": round(composite, 4),
                "feedback": f"Composite score: {composite:.4f}"
            }
            return round(composite, 4), details
        except Exception as e:
            logger.error(f"TaskMediumGrader failed: {e}")
            return 0.0, {"error": str(e)}

    def _check_orders_schema(self, engine: Engine) -> float:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='orders'"))
            cols = {row[0] for row in result.fetchall()}
            removed = {"address", "city", "postal_code", "shipped_at"}
            required = {"id", "user_id", "total", "status", "created_at"}
            if not required.issubset(cols): return 0.0
            if any(c in cols for c in removed): return 0.5
            return 1.0

    def _check_shipments_schema(self, engine: Engine) -> float:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='shipments'"))
            cols = {row[0] for row in result.fetchall()}
            required = {"id", "order_id", "address", "city", "postal_code", "shipped_at"}
            if not required.issubset(cols): return 0.0
            # Check FK
            fk_result = conn.execute(text("""
                SELECT COUNT(*) FROM information_schema.referential_constraints 
                WHERE constraint_name IN (
                    SELECT constraint_name FROM information_schema.key_column_usage 
                    WHERE table_name='shipments' AND column_name='order_id'
                )
            """))
            return 1.0 if fk_result.scalar() > 0 else 0.5

    def _check_row_count(self, engine: Engine, table: str, expected: int) -> float:
        try:
            with engine.connect() as conn:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                return 1.0 if count == expected else (count / expected if count < expected else 0.5)
        except:
            return 0.0
