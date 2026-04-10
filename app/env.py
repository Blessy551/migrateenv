"""
OpenEnv Interface — MigrateEnv core environment.
Implements reset() / step() / state() on a real Northwind PostgreSQL database.
"""
from __future__ import annotations
import logging
import os
import re
import time
from typing import Any, Optional

from sqlalchemy import text

from app.db.connection import get_engine, reconnect, DATABASE_URL
from app.db.loader import initialize_db
from app.inspector import get_schema_snapshot, get_row_counts
from app.sanitizer import sanitize_sql
from app.graders.composite import CompositeGrader
from app.tasks import TASK_REGISTRY
from app.tasks.base import BaseTask
from app.models import (
    Observation, StepResult, EnvState,
)

logger = logging.getLogger(__name__)

# Minimum composite reward to mark a task as successfully completed.
# Set lower than 1.0 to account for progressive scoring where timing /
# efficiency sub-scores prevent a perfect result on a fully correct migration.
# Override at runtime:  SUCCESS_THRESHOLD=0.85 uvicorn app.main:app
SUCCESS_THRESHOLD: float = float(os.environ.get("SUCCESS_THRESHOLD", "0.9"))

# Patterns considered truly dangerous (not just invalid).
# These are a subset of what the sanitizer blocks; we detect them here to
# assign info["dangerous"]=True rather than the standard -0.05 penalty.
_DANGEROUS_RE = [
    re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
    re.compile(
        r"\bDROP\s+TABLE\s+(?!(IF\s+EXISTS\s+)?product_pricing\b)",
        re.IGNORECASE | re.DOTALL,
    ),
]


def _is_dangerous(sql: str) -> bool:
    """Return True if *sql* matches any truly dangerous pattern."""
    return any(p.search(sql) for p in _DANGEROUS_RE)


class MigrateEnv:
    """
    Core OpenEnv-compliant environment.
    Thread-safety note: single-instance; in production wrap with a lock or use per-request state.
    """

    def __init__(self):
        self._task: Optional[BaseTask] = None
        self._engine = None
        self._grader = CompositeGrader()

        # State tracking
        self._initialized = False
        self._step_number = 0
        self._done = False
        self._invalid_sql_count = 0
        self._redundant_step_count = 0
        self._start_time: Optional[float] = None
        self._last_reward: Optional[float] = None
        self._last_grader_result: Optional[dict] = None
        self._last_sql: Optional[str] = None

        # Schema snapshot cache (avoid double DB queries per step)
        self._cached_schema: Optional[dict] = None
        self._cached_row_counts: Optional[dict] = None
        self._cache_step: int = -1

    # -----------------------------------------------------------------------
    # OpenEnv interface
    # -----------------------------------------------------------------------

    def reset(self, task_id: str) -> Observation:
        """
        Initialize the environment:
        1. Load fresh Northwind DB from SQL dump
        2. Set task context
        3. Return initial observation
        """
        if task_id not in TASK_REGISTRY:
            raise ValueError(f"Unknown task_id '{task_id}'. Available: {list(TASK_REGISTRY.keys())}")

        logger.info(f"[ENV] Resetting with task_id='{task_id}'")

        # --- Reload Northwind tables (Supabase: no DROP/CREATE DATABASE) ---
        initialize_db(database_url=DATABASE_URL)

        # Re-bind engine so any stale connections are cleared
        reconnect(DATABASE_URL)
        self._engine = get_engine()

        # --- Set task ---
        self._task = TASK_REGISTRY[task_id]()

        # --- Reset state ---
        self._initialized = True
        self._step_number = 0
        self._done = False
        self._invalid_sql_count = 0
        self._redundant_step_count = 0
        self._start_time = time.time()
        self._last_reward = None
        self._last_grader_result = None

        return self._build_observation()

    def step(self, sql: str) -> StepResult:
        """
        Execute a SQL action, compute reward, return StepResult.
        """
        import traceback

        if not self._initialized or self._task is None:
            raise RuntimeError("Environment not initialized. Call reset() first.")
        if self._done:
            raise RuntimeError("Episode is done. Call reset() to start a new episode.")

        try:
            self._step_number += 1
            info: dict[str, Any] = {"step": self._step_number, "sql": sql}

            # --- Empty SQL guard (before sanitizer) ---
            if not sql or not sql.strip():
                info["error"] = "Empty SQL"
                obs = self._build_observation()
                return StepResult(
                    observation=obs,
                    reward=0.0,
                    done=False,
                    info=info,
                )

            # --- Sanitize ---
            is_safe, reason = sanitize_sql(sql)
            if not is_safe:
                if _is_dangerous(sql):
                    # Truly dangerous — signal explicitly, no incremental penalty
                    logger.warning("Dangerous SQL blocked: %s", sql[:200])
                    info["error"] = reason
                    info["dangerous"] = True
                    info["sql_blocked"] = True
                    obs = self._build_observation()
                    return StepResult(
                        observation=obs,
                        reward=0.0,
                        done=False,
                        info=info,
                    )
                else:
                    self._invalid_sql_count += 1
                    reward = max(0.0, (self._last_reward or 0.0) - 0.05)
                    info["error"] = f"SQL blocked by sanitizer: {reason}"
                    info["sql_blocked"] = True
                    obs = self._build_observation()
                    return StepResult(
                        observation=obs,
                        reward=round(reward, 4),
                        done=False,
                        info=info,
                    )

            # --- Redundant step check ---
            if sql.strip() == (self._last_sql or "").strip():
                self._redundant_step_count += 1
            self._last_sql = sql.strip()

            # --- Execute SQL ---
            exec_info = self._execute_sql(sql)
            info.update(exec_info)

            if not exec_info.get("success", False):
                self._invalid_sql_count += 1
                reward = max(0.0, (self._last_reward or 0.0) - 0.05)
                obs = self._build_observation()
                return StepResult(
                    observation=obs,
                    reward=round(reward, 4),
                    done=False,
                    info=info,
                )

            # --- Grade ---
            requirements = self._task.get_target_schema_requirements()
            elapsed = time.time() - (self._start_time or time.time())

            grader_result = self._grader.compute(
                engine=self._engine,
                requirements=requirements,
                step_number=self._step_number,
                max_steps=self._task.max_steps,
                invalid_sql_count=self._invalid_sql_count,
                redundant_step_count=self._redundant_step_count,
                elapsed_seconds=elapsed,
            )
            self._last_grader_result = grader_result
            reward = grader_result["composite_reward"]

            # --- Mild SELECT penalty: discourage excessive inspection steps ---
            # Only penalise pure SELECT queries; all other DDL/DML is unaffected.
            if sql.strip().upper().startswith("SELECT"):
                reward = round(reward * 0.9, 4)
                info["select_penalty"] = True
                logger.debug("SELECT penalty applied — reward reduced to %.4f", reward)

            self._last_reward = reward
            self._cached_schema = None  # grader consumed DB state; clear cache

            # --- Check done ---
            max_steps_reached = self._step_number >= self._task.max_steps
            task_complete = reward >= SUCCESS_THRESHOLD
            self._done = task_complete or max_steps_reached

            info["grader"] = grader_result
            info["task_complete"] = task_complete
            info["max_steps_reached"] = max_steps_reached

            obs = self._build_observation()
            return StepResult(
                observation=obs,
                reward=round(reward, 4),
                done=self._done,
                info=info,
            )

        except Exception as e:
            logger.error("Unhandled error in step(): %s", traceback.format_exc())
            # Never crash the server — return a safe fallback result
            try:
                obs = self._build_observation()
            except Exception:
                # Absolute last resort: build a minimal bare Observation
                obs = Observation(
                    task_id=self._task.task_id if self._task else "",
                    task_description="",
                    difficulty="",
                    hint="",
                    current_schema={},
                    row_counts={},
                    step_number=self._step_number,
                    max_steps=self._task.max_steps if self._task else 0,
                    target_description="",
                )
            return StepResult(
                observation=obs,
                reward=0.0,
                done=False,
                info={"error": str(e), "step": self._step_number},
            )

    def state(self) -> EnvState:
        elapsed = time.time() - (self._start_time or time.time()) if self._start_time else 0.0
        return EnvState(
            initialized=self._initialized,
            current_task_id=self._task.task_id if self._task else None,
            step_number=self._step_number,
            max_steps=self._task.max_steps if self._task else 0,
            done=self._done,
            invalid_sql_count=self._invalid_sql_count,
            redundant_step_count=self._redundant_step_count,
            elapsed_seconds=round(elapsed, 2),
            last_reward=self._last_reward,
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _execute_sql(self, sql: str) -> dict[str, Any]:
        """Execute SQL against the live DB, return execution info dict.

        Only reconnects on genuine connection-level errors (OperationalError),
        not on every SQL syntax/semantic error, to avoid rebuilding the SSL
        connection pool on every invalid agent action.
        """
        from sqlalchemy.exc import OperationalError
        try:
            with self._engine.begin() as conn:
                conn.execute(text(sql))
            return {"success": True, "sql_executed": sql}
        except OperationalError as e:
            # True connection drop — reconnect and retry once
            logger.warning(f"DB connection error, attempting reconnect: {e}")
            try:
                reconnect(DATABASE_URL)
                self._engine = get_engine()
                logger.info("DB reconnected — retrying SQL")
                with self._engine.begin() as conn:
                    conn.execute(text(sql))
                return {"success": True, "sql_executed": sql, "reconnected": True}
            except Exception as e2:
                logger.error(f"SQL still failing after reconnect: {e2}")
                return {"success": False, "error": str(e2), "sql_executed": sql}
        except Exception as e:
            # SQL syntax/semantic error — report it cleanly, no reconnect needed
            logger.warning(f"SQL execution error: {e} | SQL: {sql[:200]}")
            return {"success": False, "error": str(e), "sql_executed": sql}

    def _build_observation(self) -> Observation:
        """Build the current observation from DB state."""
        if self._engine is None or not self._initialized:
            return Observation(
                task_id="",
                task_description="",
                difficulty="",
                hint="",
                current_schema={},
                row_counts={},
                step_number=0,
                max_steps=0,
                target_description="",
            )

        try:
            if self._cache_step == self._step_number and self._cached_schema is not None:
                schema = self._cached_schema
                row_counts = self._cached_row_counts
            else:
                schema = get_schema_snapshot(self._engine)
                row_counts = get_row_counts(self._engine)
                self._cached_schema = schema
                self._cached_row_counts = row_counts
                self._cache_step = self._step_number
        except Exception as e:
            logger.error(f"Failed to build observation: {e}")
            schema = {}
            row_counts = {}

        initial_data = self._task.get_initial_observation_data() if self._task else {}
        return Observation(
            task_id=self._task.task_id,
            task_description=self._task.description,
            difficulty=self._task.difficulty,
            hint=self._task.get_hint(),
            current_schema=schema,
            row_counts=row_counts,
            step_number=self._step_number,
            max_steps=self._task.max_steps,
            target_description=self._task.target_description,
            focus_tables=initial_data.get("focus_tables", []),
        )

    def get_last_grader_result(self) -> Optional[dict]:
        return self._last_grader_result

    def list_tasks(self) -> list[dict]:
        return [TASK_REGISTRY[k]().get_meta() for k in TASK_REGISTRY]
