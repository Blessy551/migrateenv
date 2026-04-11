"""
Task 1 Grader - Specific checks for is_verified column addition.

PRD Grading:
- Column exists with correct type/default: 0.4 points
- All rows have is_verified set (not NULL): 0.3 points
- Rows > 30 days old have is_verified=true: 0.3 points
- Max: 1.0 points
"""
from __future__ import annotations
import logging
from typing import Any
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


class TaskEasyGrader:
    """Grade Task 1: is_verified column addition."""

    def grade(self, engine: Engine, requirements: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """
        Grade Task 1 against PRD requirements.
        Returns (score, details) where score is [0.0, 1.0].
        """
        try:
            schema_score, schema_details = self._grade_schema(engine, requirements)
            data_score, data_details = self._grade_data_integrity(engine, requirements)
            backfill_score, backfill_details = self._grade_backfill(engine, requirements)
            
            # Composite (sum of weighted scores)
            composite = (schema_score * 0.4) + (data_score * 0.3) + (backfill_score * 0.3)
            
            feedback_parts = []
            
            if schema_score < 1.0:
                feedback_parts.append(f"Schema: {schema_score*100:.0f}% - {schema_details.get('feedback', '')}")
            
            if data_score < 1.0:
                feedback_parts.append(f"Data integrity: {data_score*100:.0f}% - {data_details.get('feedback', '')}")
            
            if backfill_score < 1.0:
                feedback_parts.append(f"Backfill: {backfill_score*100:.0f}% - {backfill_details.get('feedback', '')}")
            
            feedback = " | ".join(feedback_parts) if feedback_parts else "✓ Task complete!"
            
            details = {
                "schema_score": round(schema_score, 4),
                "data_integrity_score": round(data_score, 4),
                "backfill_score": round(backfill_score, 4),
                "composite_score": round(composite, 4),
                "feedback": feedback,
                "schema_details": schema_details,
                "data_details": data_details,
                "backfill_details": backfill_details,
            }
            
            logger.debug(
                f"TaskEasyGrader: schema={schema_score:.3f} data={data_score:.3f} backfill={backfill_score:.3f} → composite={composite:.4f}"
            )
            
            return round(composite, 4), details
        
        except Exception as e:
            logger.error(f"TaskEasyGrader failed: {e}", exc_info=True)
            return 0.0, {"error": str(e), "feedback": "Grader error"}

    def _grade_schema(self, engine: Engine, requirements: dict) -> tuple[float, dict]:
        """
        Grade Schema (40% weight):
        - Column is_verified exists
        - Type is BOOLEAN
        - DEFAULT false
        - NOT NULL constraint
        """
        try:
            with engine.connect() as conn:
                # Query: does is_verified column exist?
                result = conn.execute(text("""
                    SELECT column_name, data_type, column_default, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = 'users' AND column_name = 'is_verified'
                """))
                
                row = result.fetchone()
                
                if not row:
                    return 0.0, {
                        "feedback": "Column is_verified does not exist",
                        "exists": False
                    }
                
                column_name, data_type, column_default, is_nullable = row
                
                checks = {
                    "type_is_boolean": data_type.lower() in ("boolean", "bool"),
                    "has_default": column_default is not None and "false" in str(column_default).lower(),
                    "not_null": is_nullable == "NO",
                }
                
                # Score: 1.0 if all checks pass, else pro-rata
                passed = sum(checks.values())
                score = passed / len(checks)
                
                feedback_parts = []
                if not checks["type_is_boolean"]:
                    feedback_parts.append(f"Type is {data_type}, expected BOOLEAN")
                if not checks["has_default"]:
                    feedback_parts.append(f"Default is {column_default}, expected 'false'")
                if not checks["not_null"]:
                    feedback_parts.append(f"Column is nullable, expected NOT NULL")
                
                feedback = "; ".join(feedback_parts) if feedback_parts else "Column schema correct"
                
                return score, {
                    "feedback": feedback,
                    "checks": checks,
                    "details": {
                        "type": data_type,
                        "default": column_default,
                        "nullable": is_nullable,
                    }
                }
        
        except Exception as e:
            logger.error(f"Schema grade failed: {e}")
            return 0.0, {"error": str(e)}

    def _grade_data_integrity(self, engine: Engine, requirements: dict) -> tuple[float, dict]:
        """
        Grade Data Integrity (30% weight):
        - All 50 rows have is_verified set (not NULL)
        """
        try:
            with engine.connect() as conn:
                # Count NULLs in is_verified column
                result = conn.execute(text("""
                    SELECT COUNT(*) as total_rows,
                           COUNT(is_verified) as non_null_count
                    FROM users
                """))
                
                total_rows, non_null_count = result.fetchone()
                
                if total_rows == 0:
                    return 0.0, {
                        "feedback": "No rows in users table",
                        "total_rows": 0,
                        "non_null_count": 0
                    }
                
                # Score: non_null_count / total_rows
                score = non_null_count / total_rows
                
                null_count = total_rows - non_null_count
                feedback = f"{non_null_count}/{total_rows} rows have is_verified set"
                if null_count > 0:
                    feedback += f" ({null_count} NULL values)"
                
                return score, {
                    "feedback": feedback,
                    "total_rows": total_rows,
                    "non_null_count": non_null_count,
                    "null_count": null_count,
                }
        
        except Exception as e:
            logger.error(f"Data integrity grade failed: {e}")
            return 0.0, {"error": str(e)}

    def _grade_backfill(self, engine: Engine, requirements: dict) -> tuple[float, dict]:
        """
        Grade Backfill (30% weight):
        - Users created > 30 days ago must have is_verified = true
        """
        try:
            with engine.connect() as conn:
                # Count users > 30 days old
                result = conn.execute(text("""
                    SELECT COUNT(*) as total_old_users,
                           COUNT(CASE WHEN is_verified = true THEN 1 END) as correctly_set
                    FROM users
                    WHERE created_at > NOW() - INTERVAL '30 days'
                """))
                
                total_old_users, correctly_set = result.fetchone()
                
                if total_old_users == 0:
                    # No users > 30 days old, so backfill check is N/A
                    # Score: 1.0 (nothing to backfill)
                    return 1.0, {
                        "feedback": "No users > 30 days old to backfill",
                        "total_old_users": 0,
                        "correctly_set": 0,
                    }
                
                # Score: correctly_set / total_old_users
                score = correctly_set / total_old_users
                
                feedback = f"{correctly_set}/{total_old_users} old users have is_verified=true"
                incorrect = total_old_users - correctly_set
                if incorrect > 0:
                    feedback += f" ({incorrect} should be true)"
                
                return score, {
                    "feedback": feedback,
                    "total_old_users": total_old_users,
                    "correctly_set": correctly_set,
                    "incorrectly_set": incorrect,
                }
        
        except Exception as e:
            logger.error(f"Backfill grade failed: {e}")
            return 0.0, {"error": str(e)}
