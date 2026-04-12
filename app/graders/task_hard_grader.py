"""
Task 3 Grader (Hard): v1 to v3 Multi-Table Migration.

PRD Grading (0.35 + 0.25 + 0.15 + 0.10 + 0.10):
- Schema diff score: 0.35
- Data integrity (row counts): 0.25
- users name split correctly: 0.15
- products price coercion valid: 0.10
- partial index present: 0.10
"""
from __future__ import annotations
import logging
from typing import Any
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

class TaskHardGrader:
    def grade(self, engine: Engine, requirements: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        try:
            schema_score = self._check_schema(engine)
            data_integrity_score = self._check_data_integrity(engine)
            name_split_score = self._check_name_split(engine)
            price_coercion_score = self._check_price_coercion(engine)
            index_score = self._check_index(engine)
            
            composite = (schema_score * 0.35 + 
                         data_integrity_score * 0.25 + 
                         name_split_score * 0.15 + 
                         price_coercion_score * 0.10 + 
                         index_score * 0.10)
            
            details = {
                "schema_score": round(schema_score, 4),
                "data_integrity_score": round(data_integrity_score, 4),
                "name_split_score": round(name_split_score, 4),
                "price_coercion_score": round(price_coercion_score, 4),
                "index_score": round(index_score, 4),
                "composite_score": round(composite, 4),
                "feedback": f"Hard task score: {composite:.4f}"
            }
            return round(composite, 4), details
        except Exception as e:
            logger.error(f"TaskHardGrader failed: {e}")
            return 0.0, {"error": str(e)}

    def _check_schema(self, engine: Engine) -> float:
        # Simplified: check for discounts table and users.first_name/last_name
        with engine.connect() as conn:
            tables = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).fetchall()
            tablenames = {t[0] for t in tables}
            if "discounts" not in tablenames:
                return 0.25
            cols = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='users'")).fetchall()
            colnames = {c[0] for c in cols}
            if "first_name" not in colnames or "last_name" not in colnames:
                return 0.5
            return 1.0

    def _check_data_integrity(self, engine: Engine) -> float:
        try:
            with engine.connect() as conn:
                u = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
                p = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
                o = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar()
                if u == 50 and p == 20 and o == 100:
                    return 1.0
                return 0.25
        except: return 0.0

    def _check_name_split(self, engine: Engine) -> float:
        try:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT first_name, last_name FROM users ORDER BY id LIMIT 1")).fetchone()
                if row and row[0] == 'User' and row[1] == '0 Last':
                    return 1.0
                if row and row[0] == 'User' and row[1]:
                    return 0.75
                return 0.0
        except: return 0.0

    def _check_price_coercion(self, engine: Engine) -> float:
        try:
            with engine.connect() as conn:
                res = conn.execute(text("SELECT data_type FROM information_schema.columns WHERE table_name='products' AND column_name='price_new'")).scalar()
                if res in ('numeric', 'decimal'):
                    non_null = conn.execute(text("SELECT COUNT(*) FROM products WHERE price_new IS NOT NULL")).scalar()
                    total = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
                    return 1.0 if non_null == total else 0.5
                return 0.0
        except: return 0.0

    def _check_index(self, engine: Engine) -> float:
        try:
            with engine.connect() as conn:
                res = conn.execute(text("SELECT COUNT(*) FROM pg_indexes WHERE tablename='orders' AND indexname='idx_orders_uncompleted'")).scalar()
                return 1.0 if res > 0 else 0.0
        except: return 0.0
