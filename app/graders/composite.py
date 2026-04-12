"""
Composite Grader — combines all sub-scores into a final reward in [0.0, 1.0].

Weights:
  schema_match:        0.35
  data_integrity:      0.30
  relational_integrity: 0.20
  step_efficiency:     0.10
  time_score:          0.05

Penalties (subtracted after weighting):
  invalid_sql:   -0.05 per invalid SQL attempt  (capped at -0.25)
  redundant_step: -0.02 per redundant step      (capped at -0.10)

Final reward is clamped to [0.0, 1.0].
"""
from __future__ import annotations
import math
import time
import logging
from typing import Any

from sqlalchemy.engine import Engine

from app.graders.schema_grader import SchemaGrader
from app.graders.data_grader import DataGrader
from app.graders.fk_grader import FKGrader
from app.graders.task_easy_grader import TaskEasyGrader
from app.graders.task_medium_grader import TaskMediumGrader
from app.graders.task_hard_grader import TaskHardGrader
from app.inspector import table_exists

logger = logging.getLogger(__name__)

WEIGHTS = {
    "schema":          0.35,
    "data":            0.30,
    "fk":              0.20,
    "migration_bonus": 0.05,   # incremental: tables created
    "efficiency":      0.05,   # reduced from 0.10 to fit migration_bonus
    "time":            0.05,
}

PENALTY_INVALID_SQL = 0.05
PENALTY_REDUNDANT = 0.02
MAX_PENALTY_INVALID = 0.25
MAX_PENALTY_REDUNDANT = 0.10


class CompositeGrader:
    def __init__(self):
        self._schema_grader = SchemaGrader()
        self._data_grader = DataGrader()
        self._fk_grader = FKGrader()
        self._task_graders = {
            "easy": TaskEasyGrader(),
            "medium": TaskMediumGrader(),
            "hard": TaskHardGrader(),
        }

    def compute(
        self,
        engine: Engine,
        task_id: str,
        requirements: dict[str, Any],
        step_number: int,
        max_steps: int,
        invalid_sql_count: int,
        redundant_step_count: int,
        elapsed_seconds: float,
        rollback_bonus: float = 0.0,
        max_time_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """
        Returns full grader result including composite reward and all sub-scores.
        """
        task_grader = self._task_graders.get(task_id)
        if task_grader is not None:
            return self._compute_task_grade(
                task_grader=task_grader,
                engine=engine,
                requirements=requirements,
                step_number=step_number,
                max_steps=max_steps,
                invalid_sql_count=invalid_sql_count,
                redundant_step_count=redundant_step_count,
                elapsed_seconds=elapsed_seconds,
                rollback_bonus=rollback_bonus,
                max_time_seconds=max_time_seconds,
            )

        # --- Sub-scores ---
        try:
            schema_score, schema_details = self._schema_grader.score(engine, requirements)
        except Exception as e:
            logger.error("SchemaGrader failed: %s", e)
            schema_score, schema_details = 0.0, {"error": str(e)}

        try:
            data_score, data_details = self._data_grader.score(engine, requirements)
        except Exception as e:
            logger.error("DataGrader failed: %s", e)
            data_score, data_details = 0.0, {"error": str(e)}

        try:
            fk_score, fk_details = self._fk_grader.score(engine, requirements)
        except Exception as e:
            logger.error("FKGrader failed: %s", e)
            fk_score, fk_details = 0.0, {"error": str(e)}

        # --- Migration bonus: reward table creation incrementally ---
        migration_bonus = self._compute_migration_bonus(engine, requirements)

        # --- Efficiency score: fewer steps = higher score ---
        if max_steps > 0:
            steps_used_ratio = step_number / max_steps
            # Full score if ≤ 50% of steps used, drops linearly to 0 at 100%
            efficiency_score = max(0.0, 1.0 - max(0.0, (steps_used_ratio - 0.5) * 2.0))
        else:
            efficiency_score = 0.5

        # --- Time score: faster = higher ---
        time_ratio = min(elapsed_seconds / max(max_time_seconds, 1.0), 1.0)
        time_score = max(0.0, 1.0 - time_ratio)

        # --- Weighted composite ---
        weighted = (
            WEIGHTS["schema"]          * schema_score
            + WEIGHTS["data"]            * data_score
            + WEIGHTS["fk"]              * fk_score
            + WEIGHTS["migration_bonus"] * migration_bonus
            + WEIGHTS["efficiency"]      * efficiency_score
            + WEIGHTS["time"]            * time_score
        )

        # --- Penalties ---
        invalid_penalty = min(invalid_sql_count * PENALTY_INVALID_SQL, MAX_PENALTY_INVALID)
        redundant_penalty = min(redundant_step_count * PENALTY_REDUNDANT, MAX_PENALTY_REDUNDANT)
        total_penalty = invalid_penalty + redundant_penalty

        # --- Final reward ---
        composite_reward = max(0.0, min(1.0, weighted - total_penalty))

        result = {
            "schema_score":       round(schema_score, 4),
            "data_score":         round(data_score, 4),
            "fk_score":           round(fk_score, 4),
            "migration_bonus":    round(migration_bonus, 4),
            "efficiency_score":   round(efficiency_score, 4),
            "time_score":         round(time_score, 4),
            "composite_reward":   round(composite_reward, 4),
            # Human-readable feedback bubbled up from SchemaGrader
            "feedback":           schema_details.get("feedback", "Schema looks correct"),
            "penalties": {
                "invalid_sql_count":      invalid_sql_count,
                "invalid_sql_penalty":    round(invalid_penalty, 4),
                "redundant_step_count":   redundant_step_count,
                "redundant_step_penalty": round(redundant_penalty, 4),
                "total_penalty":          round(total_penalty, 4),
            },
            "details": {
                "schema":           schema_details,
                "data":             data_details,
                "fk":               fk_details,
                "step_number":      step_number,
                "max_steps":        max_steps,
                "elapsed_seconds":  round(elapsed_seconds, 2),
            },
        }

        logger.debug(
            "Grader: schema=%.3f data=%.3f fk=%.3f migration_bonus=%.3f "
            "eff=%.3f time=%.3f → reward=%.4f",
            schema_score, data_score, fk_score, migration_bonus,
            efficiency_score, time_score, composite_reward,
        )

        return result

    def _compute_task_grade(
        self,
        task_grader,
        engine: Engine,
        requirements: dict[str, Any],
        step_number: int,
        max_steps: int,
        invalid_sql_count: int,
        redundant_step_count: int,
        elapsed_seconds: float,
        rollback_bonus: float,
        max_time_seconds: float,
    ) -> dict[str, Any]:
        base_score, details = task_grader.grade(engine, requirements)
        efficiency_score = max(0.0, 1.0 - (step_number / max(max_steps, 1)))
        time_ratio = min(elapsed_seconds / max(max_time_seconds, 1.0), 1.0)
        time_penalty = max(0.0, time_ratio - 0.8) if time_ratio > 0.8 else 0.0
        invalid_penalty = min(invalid_sql_count * PENALTY_INVALID_SQL, MAX_PENALTY_INVALID)
        redundant_penalty = min(redundant_step_count * PENALTY_REDUNDANT, MAX_PENALTY_REDUNDANT)
        total_penalty = invalid_penalty + redundant_penalty + time_penalty
        composite_reward = max(0.0, min(1.0, base_score - total_penalty + rollback_bonus))
        return {
            "schema_score": round(details.get("schema_score", details.get("orders_schema_score", details.get("composite_score", base_score))), 4),
            "data_score": round(
                details.get(
                    "data_integrity_score",
                    (
                        details.get("orders_data_score", 0.0) + details.get("shipments_data_score", 0.0)
                    ) / 2 if "orders_data_score" in details else details.get("composite_score", base_score)
                ),
                4,
            ),
            "fk_score": round(details.get("shipments_schema_score", details.get("index_score", 1.0)), 4),
            "efficiency_score": round(efficiency_score, 4),
            "time_score": round(max(0.0, 1.0 - time_ratio), 4),
            "rollback_bonus": round(rollback_bonus, 4),
            "composite_reward": round(composite_reward, 4),
            "feedback": details.get("feedback", "Task grading complete"),
            "penalties": {
                "invalid_sql_count": invalid_sql_count,
                "invalid_sql_penalty": round(invalid_penalty, 4),
                "redundant_step_count": redundant_step_count,
                "redundant_step_penalty": round(redundant_penalty, 4),
                "time_penalty": round(time_penalty, 4),
                "total_penalty": round(total_penalty, 4),
            },
            "details": {
                "task_grade": details,
                "step_number": step_number,
                "max_steps": max_steps,
                "elapsed_seconds": round(elapsed_seconds, 2),
            },
        }

    # ------------------------------------------------------------------
    def _compute_migration_bonus(self, engine, requirements: dict) -> float:
        """
        Incremental reward for table creation progress.
        Returns fraction of required_tables that currently exist [0.0, 1.0].
        Returns 0.0 if no required_tables are specified (non-migration tasks).
        """
        required_tables: list[str] = requirements.get("required_tables", [])
        if not required_tables:
            return 0.0
        try:
            found = sum(1 for t in required_tables if table_exists(engine, t))
            return found / len(required_tables)
        except Exception as e:
            logger.warning("_compute_migration_bonus failed: %s", e)
            return 0.0
