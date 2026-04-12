"""
Pydantic models for all FastAPI request/response bodies.
"""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ResetRequest(BaseModel):
    task_id: str = Field(default="easy", description="Task ID to initialize: 'easy', 'medium', or 'hard'")


class StepRequest(BaseModel):
    action_type: Literal["inspect", "execute", "rollback", "done"] = Field(
        default="execute",
        description="Type of action to perform",
    )
    sql: Optional[str] = Field(
        default=None,
        description="Single SQL statement to execute as the agent's action",
    )
    inspect_query: Optional[str] = Field(
        default=None,
        description="High-level schema/data inspection request",
    )
    session_id: str = Field(..., description="Session ID for environment state continuity")


class Action(BaseModel):
    action_type: Literal["inspect", "execute", "rollback", "done"]
    sql: Optional[str] = None
    inspect_query: Optional[str] = None


class ResetResponse(BaseModel):
    session_id: str
    observation: Observation


# ---------------------------------------------------------------------------
# Response / shared models
# ---------------------------------------------------------------------------

class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool
    default: Optional[str] = None


class TableSchema(BaseModel):
    columns: list[ColumnInfo]
    primary_keys: list[str]
    foreign_keys: list[dict[str, Any]]
    indexes: list[dict[str, Any]]
    check_constraints: list[dict[str, Any]]
    unique_constraints: list[dict[str, Any]]


class Observation(BaseModel):
    task_id: str
    task_description: str
    difficulty: str
    hint: str
    target_spec: dict[str, Any] = Field(default_factory=dict, description="Technical specification of the target schema")
    current_schema: dict[str, Any]
    row_counts: dict[str, int]
    step_number: int
    max_steps: int
    target_description: str
    last_action_result: str = Field(default="", description="Plain-text result of the most recent action")
    focus_tables: list[str] = Field(default_factory=list, description="Tables the agent should focus on for this task")


class Reward(BaseModel):
    total: float = Field(..., ge=0.0, le=1.0)
    schema_match: float = Field(default=0.0, ge=0.0, le=1.0)
    data_integrity: float = Field(default=0.0, ge=0.0, le=1.0)
    fk_integrity: float = Field(default=0.0, ge=0.0, le=1.0)
    step_efficiency: float = Field(default=0.0, ge=0.0, le=1.0)
    time_penalty: float = Field(default=0.0, ge=0.0)
    rollback_bonus: float = Field(default=0.0, ge=0.0, le=1.0)


class StepResult(BaseModel):
    observation: Observation
    reward: float = Field(..., ge=0.0, le=1.0)
    done: bool
    info: dict[str, Any]
    reward_model: Optional[Reward] = None


class GraderResult(BaseModel):
    schema_score: float
    data_score: float
    fk_score: float
    efficiency_score: float
    time_score: float
    composite_reward: float
    penalties: dict[str, float]
    details: dict[str, Any]


class TaskMeta(BaseModel):
    task_id: str
    difficulty: str
    description: str
    max_steps: int
    target_reward: float
    target_description: str


class EnvState(BaseModel):
    initialized: bool
    current_task_id: Optional[str]
    step_number: int
    max_steps: int
    done: bool
    invalid_sql_count: int
    redundant_step_count: int
    elapsed_seconds: float
    last_reward: Optional[float]


class HealthResponse(BaseModel):
    status: str
    database_connected: bool
    current_task: Optional[str]
    uptime_seconds: float


class BaselineResult(BaseModel):
    task_id: str
    difficulty: str
    steps_taken: int
    final_reward: float
    composite_score: float
    success: bool
    actions: list[str]
