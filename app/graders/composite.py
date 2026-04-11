"""
Composite Grader — Refactored for PRD Alignment.

Maintains the overall reward calculation using specific task graders
and applying PRD-defined weights and penalties.

PRD Formula:
  R_total = (
    0.45 × schema_match_score
    + 0.25 × data_integrity_score
    - 0.15 × step_penalty
    - 0.10 × time_penalty
    + 0.05 × rollback_bonus
  )
"""
from __future__ import annotations
import math
import logging
from typing import Any
from sqlalchemy.engine import Engine

from app.graders.task_easy_grader import TaskEasyGrader
from app.graders.task_medium_grader import TaskMediumGrader
from app.graders.task_hard_grader import TaskHardGrader

logger = logging.getLogger(__name__)

class CompositeGrader:
    def __init__(self):
        self._graders = {
            "easy": TaskEasyGrader(),
            "medium": TaskMediumGrader(),
            "hard": TaskHardGrader(),
        }

    def compute(
        self,
        engine: Engine,
        requirements: dict[str, Any],
        step_number: int,
        max_steps: int,
        invalid_sql_count: int,
        redundant_step_count: int,
        elapsed_seconds: float,
        task_id: str = "easy",
        time_limit: float = 300.0,
    ) -> dict[str, Any]:
        
        grader = self._graders.get(task_id, self._graders["easy"])
        score, details = grader.grade(engine, requirements)
        
        # PRD Components
        schema_match = score # Task-specific composite score
        data_integrity = details.get("data_integrity_score", details.get("orders_data_score", details.get("data_integrity_score", 1.0)))
        
        # Penalties
        step_penalty = 0.15 * (math.sqrt(step_number / max_steps) if max_steps > 0 else 0)
        
        time_penalty = 0.0
        if elapsed_seconds > (time_limit * 0.8):
            time_penalty = 0.10 * (elapsed_seconds / time_limit)
            
        # Placeholder for rollback bonus (requires SQL tracking)
        rollback_bonus = 0.0 
        
        # Weighted formula
        weighted = (
            0.45 * schema_match
            + 0.25 * data_integrity
            - step_penalty
            - time_penalty
            + rollback_bonus
        )
        
        composite_reward = max(0.0, min(1.0, weighted))
        
        result = {
            "schema_score": round(schema_match, 4),
            "data_score": round(data_integrity, 4),
            "composite_reward": round(composite_reward, 4),
            "feedback": details.get("feedback", ""),
            "penalties": {
                "step_penalty": round(step_penalty, 4),
                "time_penalty": round(time_penalty, 4),
                "invalid_sql_count": invalid_sql_count,
            },
            "details": details
        }
        
        return result
