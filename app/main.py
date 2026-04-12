"""
FastAPI application — exposes all 7 OpenEnv endpoints.
"""
from __future__ import annotations
import logging
import time
import uuid
import os
from typing import Any, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.env import MigrateEnv
from app.models import (
    ResetRequest, StepRequest, ResetResponse,
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

# Global dictionary for session tracking
SESSIONS: dict = {}

# ---------------------------------------------------------------------------
# FIX: Do NOT instantiate MigrateEnv() at module level.
# Doing so triggers app/db/connection.py imports which previously crashed
# when DATABASE_URL was missing. Now we create a fallback env lazily.
# ---------------------------------------------------------------------------
_fallback_env: MigrateEnv | None = None
_startup_time = time.time()
OPENENV_PATH = Path(__file__).resolve().parent.parent / "openenv.yaml"


def _get_fallback_env() -> MigrateEnv:
    """Return (and lazily create) the module-level fallback env instance."""
    global _fallback_env
    if _fallback_env is None:
        _fallback_env = MigrateEnv()
    return _fallback_env


def _get_active_env(session_id: str | None = None) -> MigrateEnv:
    if session_id and session_id in SESSIONS:
        return SESSIONS[session_id]["env"]
    if SESSIONS:
        last_session_id = next(reversed(SESSIONS))
        return SESSIONS[last_session_id]["env"]
    return _get_fallback_env()

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", tags=["system"])
def root():
    """Root endpoint — returns service info."""
    return {"name": "MigrateEnv", "version": "1.0.0", "status": "ok"}


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health():
    """Health check — returns database connectivity and environment status."""
    db_ok = ping_db()
    uptime = round(time.time() - _startup_time, 1)
    active_env = _get_active_env()
    current_task = active_env._task.task_id if active_env._task else None
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


@app.post("/reset", response_model=ResetResponse, tags=["env"])
def reset(request: Optional[ResetRequest] = None):
    """
    Initialize the environment for a given task.
    Supports POST /reset with or without a body (OpenEnv validator compatibility).
    """
    try:
        task_id = request.task_id if request and request.task_id else "easy"

        env_instance = MigrateEnv()
        obs = env_instance.reset(task_id)

        session_id = str(uuid.uuid4())
        SESSIONS[session_id] = {
            "env": env_instance,
            "state": obs,
        }

        logger.info(f"Reset successful for task '{task_id}', session: {session_id}")
        return ResetResponse(session_id=session_id, observation=obs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Reset failed: {e}")
        raise HTTPException(status_code=500, detail=f"Reset failed: {str(e)}")


@app.post("/step", tags=["env"])
def step(request: StepRequest):
    """
    Execute a SQL action in the environment.
    """
    session_id = request.session_id
    logger.debug("Received step with session_id=%s", session_id)

    if session_id not in SESSIONS:
        logger.error("session_id %s not found", session_id)
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid session_id"}
        )

    logger.debug("session_id %s found, continuing episode", session_id)
    env_instance = SESSIONS[session_id]["env"]
    logger.debug("Step BEFORE: env_id=%s", id(env_instance))

    try:
        result = env_instance.step(
            {
                "action_type": request.action_type,
                "sql": request.sql,
                "inspect_query": request.inspect_query,
            }
        )
        logger.debug(
            "Step AFTER: env_id=%s done=%s reward=%s",
            id(env_instance),
            result.done,
            result.reward,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        logger.error(f"Step failed unexpectedly: {e}\n{traceback.format_exc()}")
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
                    "last_action_result": str(e),
                    "focus_tables": [],
                },
            },
        )


@app.get("/state", response_model=EnvState, tags=["env"])
def state(session_id: str | None = None):
    """Get current internal state of the environment."""
    return _get_active_env(session_id).state()


@app.get("/grader", tags=["evaluation"])
def grader(session_id: str | None = None):
    """
    Get the current grader scores for the active task.
    Returns 0.0 for all scores if no task is active.
    """
    active_env = _get_active_env(session_id)
    if not active_env._initialized or active_env._task is None:
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

    last = active_env.get_last_grader_result()
    if last is None:
        env_state = active_env.state()
        requirements = active_env._task.get_target_schema_requirements()
        result = active_env._grader.compute(
            engine=active_env._engine,
            task_id=active_env._task.task_id,
            requirements=requirements,
            step_number=env_state.step_number,
            max_steps=env_state.max_steps,
            invalid_sql_count=env_state.invalid_sql_count,
            redundant_step_count=env_state.redundant_step_count,
            elapsed_seconds=env_state.elapsed_seconds,
        )
        return result

    return last


@app.get("/openenv.yaml", tags=["system"])
def get_openenv_yaml():
    """Serve the OpenEnv metadata file."""
    return FileResponse(OPENENV_PATH, media_type="text/yaml", filename="openenv.yaml")

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