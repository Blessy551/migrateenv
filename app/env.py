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
    Action, Observation, Reward, StepResult, EnvState,
)

logger = logging.getLogger(__name__)

# Override at runtime if a task omits an explicit reward target.
SUCCESS_THRESHOLD: float = float(os.environ.get("SUCCESS_THRESHOLD", "0.85"))
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
        self._last_action_result: str = ""
        self._last_action_error: Optional[str] = None
        self._inspect_count = 0
        self._execute_count = 0
        self._rollback_bonus = 0.0

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
        initialize_db(task_id=task_id, database_url=DATABASE_URL)

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
        self._last_sql = None
        self._last_action_result = "Environment reset successfully."
        self._last_action_error = None
        self._inspect_count = 0
        self._execute_count = 0
        self._rollback_bonus = 0.0

        return self._build_observation()

    def step(self, action: str | Action) -> StepResult:
        """
        Execute an action, compute reward, return StepResult.
        """
        import traceback

        if not self._initialized or self._task is None:
            raise RuntimeError("Environment not initialized. Call reset() first.")
        if self._done:
            raise RuntimeError("Episode is done. Call reset() to start a new episode.")

        try:
            self._step_number += 1
            parsed_action = self._normalize_action(action)
            info: dict[str, Any] = {
                "step": self._step_number,
                "action_type": parsed_action.action_type,
                "sql": parsed_action.sql,
                "inspect_query": parsed_action.inspect_query,
            }

            if parsed_action.action_type == "inspect":
                self._inspect_count += 1
                self._last_action_result = self._handle_inspect(parsed_action.inspect_query)
                reward, reward_model, grader_result = self._grade_current_state()
                if self._inspect_count > 5 and self._execute_count == 0:
                    reward = max(0.0, reward - 0.02)
                    reward_model.total = round(reward, 4)
                    info["inspect_penalty"] = 0.02
                self._last_reward = reward
                self._last_grader_result = grader_result
                info["grader"] = grader_result
                info["task_complete"] = False
                obs = self._build_observation()
                return StepResult(
                    observation=obs,
                    reward=round(reward, 4),
                    done=False,
                    info=info,
                    reward_model=reward_model,
                )

            if parsed_action.action_type == "rollback":
                self._rollback_bonus = 0.05
                self._last_action_result = "Rollback requested. No transactional rollback available; bonus recorded."
                reward, reward_model, grader_result = self._grade_current_state()
                reward = min(1.0, reward + self._rollback_bonus)
                reward_model.total = round(reward, 4)
                reward_model.rollback_bonus = self._rollback_bonus
                self._last_reward = reward
                self._last_grader_result = grader_result
                info["grader"] = grader_result
                obs = self._build_observation()
                return StepResult(
                    observation=obs,
                    reward=round(reward, 4),
                    done=False,
                    info=info,
                    reward_model=reward_model,
                )

            if parsed_action.action_type == "done":
                reward, reward_model, grader_result = self._grade_current_state()
                target_reward = getattr(self._task, "target_reward", SUCCESS_THRESHOLD) or SUCCESS_THRESHOLD
                task_complete = reward >= target_reward
                self._last_reward = reward
                self._last_grader_result = grader_result
                self._done = True
                self._last_action_result = "Final grading completed."
                info["grader"] = grader_result
                info["task_complete"] = task_complete
                info["final_grading"] = True
                obs = self._build_observation()
                return StepResult(
                    observation=obs,
                    reward=round(reward, 4),
                    done=True,
                    info=info,
                    reward_model=reward_model,
                )

            sql = parsed_action.sql or ""
            if not sql.strip():
                self._last_action_result = "Empty SQL"
                self._last_action_error = "Empty SQL"
                info["error"] = "Empty SQL"
                obs = self._build_observation()
                return StepResult(
                    observation=obs,
                    reward=0.0,
                    done=False,
                    info=info,
                    reward_model=Reward(total=0.0),
                )

            is_safe, reason = sanitize_sql(sql)
            if not is_safe:
                self._last_action_error = reason
                self._last_action_result = reason
                if _is_dangerous(sql):
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
                        reward_model=Reward(total=0.0),
                    )
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
                    reward_model=Reward(total=round(reward, 4)),
                )

            if sql.strip() == (self._last_sql or "").strip():
                self._redundant_step_count += 1
            self._last_sql = sql.strip()

            exec_info = self._execute_sql(sql)
            info.update(exec_info)

            if not exec_info.get("success", False):
                self._invalid_sql_count += 1
                self._last_action_error = exec_info.get("error", "SQL execution failed")
                self._last_action_result = self._last_action_error
                reward = max(0.0, (self._last_reward or 0.0) - 0.05)
                obs = self._build_observation()
                return StepResult(
                    observation=obs,
                    reward=round(reward, 4),
                    done=False,
                    info=info,
                    reward_model=Reward(total=round(reward, 4)),
                )

            self._execute_count += 1
            self._last_action_error = None
            self._last_action_result = "SQL executed successfully."
            self._cached_schema = None
            reward, reward_model, grader_result = self._grade_current_state()
            self._last_reward = reward
            self._last_grader_result = grader_result

            max_steps_reached = self._step_number >= self._task.max_steps
            info["grader"] = grader_result
            info["task_complete"] = False
            info["max_steps_reached"] = max_steps_reached
            obs = self._build_observation()
            return StepResult(
                observation=obs,
                reward=round(reward, 4),
                done=max_steps_reached,
                info=info,
                reward_model=reward_model,
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
                    last_action_result=self._last_action_result,
                )
            return StepResult(
                observation=obs,
                reward=0.0,
                done=False,
                info={"error": str(e), "step": self._step_number},
                reward_model=Reward(total=0.0),
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
                last_action_result=self._last_action_result,
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
        hint = ""
        if self._task:
            try:
                hint = self._task.get_hint()
            except:
                hint = ""
                
        return Observation(
            task_id=self._task.task_id,
            task_description=self._task.description,
            difficulty=self._task.difficulty,
            hint=self._task.get_hint(),
            target_spec=self._task.get_target_schema_requirements(),
            current_schema=schema,
            row_counts=row_counts,
            step_number=self._step_number,
            max_steps=self._task.max_steps,
            target_description=self._task.target_description,
            last_action_result=self._last_action_result,
            focus_tables=initial_data.get("focus_tables", []),
        )

    def _normalize_action(self, action: str | Action | dict[str, Any]) -> Action:
        if isinstance(action, Action):
            return action
        if isinstance(action, dict):
            return Action(**action)
        return Action(action_type="execute", sql=action)

    def _handle_inspect(self, inspect_query: Optional[str]) -> str:
        focus_tables = []
        if self._task:
            focus_tables = self._task.get_initial_observation_data().get("focus_tables", [])
        inspected = ", ".join(focus_tables) if focus_tables else "all tables"
        if inspect_query:
            return f"Inspection request '{inspect_query}' returned schema and row counts for {inspected}."
        return f"Returned schema and row counts for {inspected}."

    def _grade_current_state(self) -> tuple[float, Reward, dict[str, Any]]:
        requirements = self._task.get_target_schema_requirements()
        elapsed = time.time() - (self._start_time or time.time())
        grader_result = self._grader.compute(
            engine=self._engine,
            task_id=self._task.task_id,
            requirements=requirements,
            step_number=self._step_number,
            max_steps=self._task.max_steps,
            invalid_sql_count=self._invalid_sql_count,
            redundant_step_count=self._redundant_step_count,
            elapsed_seconds=elapsed,
            rollback_bonus=self._rollback_bonus,
        )
        reward = grader_result["composite_reward"]
        reward_model = Reward(
            total=round(reward, 4),
            schema_match=grader_result.get("schema_score", 0.0),
            data_integrity=grader_result.get("data_score", 0.0),
            fk_integrity=grader_result.get("fk_score", 0.0),
            step_efficiency=grader_result.get("efficiency_score", 0.0),
            time_penalty=grader_result.get("penalties", {}).get("time_penalty", 0.0),
            rollback_bonus=grader_result.get("rollback_bonus", self._rollback_bonus),
        )
        return reward, reward_model, grader_result

    def get_last_grader_result(self) -> Optional[dict]:
        return self._last_grader_result

    def list_tasks(self) -> list[dict]:
        return [TASK_REGISTRY[k]().get_meta() for k in TASK_REGISTRY]
