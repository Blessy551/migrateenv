"""
Composite Grader — combines all sub-scores into a final reward in [0.0, 1.0].

Weights:
  schema_match:        0.35
  data_integrity:      0.30
  relational_integrity: 0.20
  migration_bonus:     0.05
  step_efficiency:     0.05
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

    def compute(
        self,
        engine: Engine,
        requirements: dict[str, Any],
        step_number: int,
        max_steps: int,
        invalid_sql_count: int,
        redundant_step_count: int,
        elapsed_seconds: float,
        max_time_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """
        Returns full grader result including composite reward and all sub-scores.
        """

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
