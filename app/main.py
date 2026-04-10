"""
FastAPI application — exposes all 7 OpenEnv endpoints.
"""
from __future__ import annotations
import logging
import time
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.env import MigrateEnv
from app.models import (
    ResetRequest, StepRequest,
    Observation, StepResult, EnvState,
    GraderResult, TaskMeta, HealthResponse, BaselineResult,
)
from app.db.connection import ping_db

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="MigrateEnv",
    description=(
        "OpenEnv-compliant reinforcement learning environment for "
        "evaluating LLM agents on real-world PostgreSQL database migrations "
        "using the Northwind dataset."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single global environment instance (per process)
env = MigrateEnv()
_startup_time = time.time()

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
def health():
    """Health check — returns database connectivity and environment status."""
    db_ok = ping_db()
    uptime = round(time.time() - _startup_time, 1)
    current_task = env._task.task_id if env._task else None
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database_connected=db_ok,
        current_task=current_task,
        uptime_seconds=uptime,
    )


@app.get("/tasks", response_model=list[TaskMeta], tags=["tasks"])
def list_tasks():
    """List all available migration tasks with metadata."""
    from app.tasks import TASK_REGISTRY
    tasks = []
    for task_id, task_cls in TASK_REGISTRY.items():
        task = task_cls()
        tasks.append(TaskMeta(
            task_id=task.task_id,
            difficulty=task.difficulty,
            description=task.description,
            max_steps=task.max_steps,
            target_reward=task.target_reward,
            target_description=task.target_description,
        ))
    return tasks


@app.post("/reset", response_model=Observation, tags=["env"])
def reset(request: ResetRequest):
    """
    Initialize the environment for a given task.
    Reloads the real Northwind database from SQL dump (deterministic clean state).
    """
    try:
        obs = env.reset(request.task_id)
        logger.info(f"Reset successful for task '{request.task_id}'")
        return obs
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Reset failed: {e}")
        raise HTTPException(status_code=500, detail=f"Reset failed: {str(e)}")


@app.post("/step", tags=["env"])
def step(request: StepRequest):
    """
    Execute a SQL action in the environment.
    Returns observation, reward, done flag, and grading info.
    This endpoint NEVER returns HTTP 5xx — all errors are surfaced in the
    response body as info["error"] with reward=0 and done=False.
    """
    try:
        result = env.step(request.sql)
        return result
    except RuntimeError as e:
        # Not-initialized / already-done — return 400 with explanation
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        logger.error(f"Step failed unexpectedly: {e}\n{traceback.format_exc()}")
        # Return a guaranteed safe response — never a 500
        return JSONResponse(
            status_code=200,
            content={
                "reward": 0.0,
                "done": False,
                "info": {
                    "error": str(e),
                    "schema": 0.0,
                    "data": 0.0,
                    "fk": 0.0,
                },
                "observation": {
                    "task_id": "",
                    "task_description": "Error recovery — call /reset",
                    "difficulty": "",
                    "hint": "",
                    "current_schema": {},
                    "row_counts": {},
                    "step_number": 0,
                    "max_steps": 0,
                    "target_description": "",
                    "focus_tables": [],
                },
            },
        )


@app.get("/state", response_model=EnvState, tags=["env"])
def state():
    """Get current internal state of the environment."""
    return env.state()


@app.get("/grader", tags=["evaluation"])
def grader():
    """
    Get the current grader scores for the active task.
    Returns 0.0 for all scores if no task is active.
    """
    if not env._initialized or env._task is None:
        return {
            "schema_score": 0.0,
            "data_score": 0.0,
            "fk_score": 0.0,
            "efficiency_score": 0.0,
            "time_score": 0.0,
            "composite_reward": 0.0,
            "penalties": {},
            "details": {"note": "No active task. Call POST /reset first."},
        }

    last = env.get_last_grader_result()
    if last is None:
        # Force a grade against current state
        env_state = env.state()
        requirements = env._task.get_target_schema_requirements()
        result = env._grader.compute(
            engine=env._engine,
            requirements=requirements,
            step_number=env_state.step_number,
            max_steps=env_state.max_steps,
            invalid_sql_count=env_state.invalid_sql_count,
            redundant_step_count=env_state.redundant_step_count,
            elapsed_seconds=env_state.elapsed_seconds,
        )
        return result

    return last

# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    logger.info("MigrateEnv FastAPI server starting up...")
    db_ok = ping_db()
    if db_ok:
        logger.info("Database connection: OK")
    else:
        logger.warning(
            "Database NOT reachable on startup. "
            "Ensure PostgreSQL is running and DATABASE_URL is set correctly."
        )
