#!/usr/bin/env python3
"""
MigrateEnv Inference Script
============================
Runs the MigrateEnv baseline agent against all 3 tasks using the OpenAI client.

Required environment variables:
    API_BASE_URL   LLM endpoint (default: https://api.openai.com/v1)
    MODEL_NAME     Model identifier (default: gpt-4.1-mini)
    HF_TOKEN       API key (mandatory — no default)

Stdout format (STRICT — exactly these three line types, nothing else):
    [START] task=<id> env=migrateenv model=<model>
    [STEP]  step=<n> action=<action_str> reward=<0.00> done=<true|false> error=<msg|null>
    [END]   success=<true|false> steps=<n> rewards=<r1,r2,...,rn>

All debug/display output is fully suppressed — only spec lines reach stdout.

Usage:
    HF_TOKEN=your_key python inference.py
    HF_TOKEN=your_key python inference.py --host http://localhost:8000 --output results.json
"""
from __future__ import annotations
import os
import sys
import time
import json
import argparse
import logging

import httpx
from openai import OpenAI
from rich.console import Console

from dotenv import load_dotenv
load_dotenv()

# Silence all loggers — nothing must leak to stderr during evaluation
logging.disable(logging.CRITICAL)
logger = logging.getLogger(__name__)

# Summary table only — written to stderr AFTER all tasks complete
console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Environment variables — spec-required
# ---------------------------------------------------------------------------
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME   = os.getenv("MODEL_NAME", "gpt-4.1-mini")
HF_TOKEN     = os.getenv("HF_TOKEN")

if HF_TOKEN is None:
    raise ValueError("HF_TOKEN environment variable is required")

# ---------------------------------------------------------------------------
# OpenAI client — mandatory, no alternative SDKs
# ---------------------------------------------------------------------------
client = OpenAI(
    base_url=API_BASE_URL,
    api_key=HF_TOKEN,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_HOST      = os.getenv("MIGRATEENV_HOST", "http://localhost:8000")
SUCCESS_THRESHOLD = float(os.environ.get("SUCCESS_THRESHOLD", "0.9"))
REQUEST_TIMEOUT   = 120.0
RATE_LIMIT_SLEEP  = 2.0
BENCHMARK         = "migrateenv"

# Task definitions
TASKS = [
    {
        "id": "easy",
        "label": "Task 1 — Add column (easy)",
        "max_steps": 10,
        "time_limit": 120,
    },
    {
        "id": "medium",
        "label": "Task 2 — Table split (medium)",
        "max_steps": 20,
        "time_limit": 120,
    },
    {
        "id": "hard",
        "label": "Task 3 — Multi-table migration (hard)",
        "max_steps": 30,
        "time_limit": 300,
    },
]

# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a SQLite database migration engineer operating inside MigrateEnv.

You will receive an observation containing:
  - task_description: what needs to be migrated
  - hint: suggested SQL steps
  - current_schema: the live database schema
  - row_counts: current row counts per table

Your goal is to migrate the Northwind database to match the task requirements without data loss.

Always respond with a single JSON action using this exact format:
{"action_type": "inspect" | "execute" | "done", "sql": "...", "inspect_query": "..."}

CRITICAL RULES:
- You are working with SQLite, NOT PostgreSQL.
- SQLite does NOT support:
  - information_schema
  - SERIAL (use INTEGER PRIMARY KEY instead — it auto-increments)
  - NOW() (use CURRENT_TIMESTAMP instead)
  - SMALLINT (use INTEGER instead)
  - ALTER TABLE ... ADD CONSTRAINT (this will always fail)
  - ALTER TABLE ... ADD CHECK (this will always fail)
  - CONSTRAINT <name> FOREIGN KEY inside ALTER TABLE (declare FKs inside CREATE TABLE only)
- ALTER TABLE ... DROP COLUMN exists only in SQLite 3.35+; prefer the table rebuild pattern.
- Table names are case-sensitive (e.g., Customers, Orders, Products).

ADDING A CHECK CONSTRAINT IN SQLITE (only supported method — table rebuild):
SQLite cannot add constraints to existing tables. You must rebuild the table:
  Step 1: PRAGMA table_info('TableName') — get every column exactly
  Step 2: CREATE TABLE t_new (...all original columns..., new_col ..., CHECK (...))
  Step 3: INSERT INTO t_new SELECT <all original cols>, <new_col_value> FROM t
  Step 4: DROP TABLE t
  Step 5: ALTER TABLE t_new RENAME TO t

REMOVING COLUMNS IN SQLITE (table rebuild pattern):
  Step 1: PRAGMA table_info('TableName') — get every column exactly
  Step 2: CREATE TABLE t_new with only the columns you want to keep
  Step 3: INSERT INTO t_new SELECT <kept cols> FROM t
  Step 4: DROP TABLE t
  Step 5: ALTER TABLE t_new RENAME TO t

MANDATORY FIRST STEP:
- You MUST ALWAYS start by inspecting the database.
- First run:
  SELECT name FROM sqlite_master WHERE type='table';

- Then inspect columns using:
  PRAGMA table_info('TableName');

Rules:
- "inspect": run a read-only SQL query (SELECT) to understand current state — set inspect_query
- "execute": run a migration SQL statement (ALTER TABLE, CREATE TABLE, UPDATE, CREATE INDEX, INSERT) — set sql
- "done": signal that the migration is complete
- One action per response. No explanation. Pure JSON only.
- Do NOT use TRUNCATE, DROP DATABASE, or DROP SCHEMA.
- Do NOT assume table names — always inspect first.
- If an execute fails, correct it in the next step.
- After completing all steps, verify your work with a SELECT before sending done.
- For normalization tasks, ensure ALL listed columns are dropped from the source table.

STRATEGY:
1. Inspect tables first
2. Inspect schema of relevant tables
3. Perform migration steps
4. Verify results before finishing
"""

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def api_reset(host: str, task_id: str) -> dict:
    r = httpx.post(f"{host}/reset", json={"task_id": task_id}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def api_step(host: str, action: dict) -> dict:
    """Submit an action; maps action_type to the correct SQL field."""
    action_type = action.get("action_type", "execute")
    if action_type == "execute":
        sql = action.get("sql", "SELECT 1")
    elif action_type == "inspect":
        sql = action.get("inspect_query", "SELECT 1")
    else:  # done / noop / unknown — safe no-op
        sql = "SELECT 1"
    r = httpx.post(f"{host}/step", json={"sql": sql}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def api_health(host: str) -> dict:
    r = httpx.get(f"{host}/health", timeout=10.0)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Helpers for spec-compliant field formatting
# ---------------------------------------------------------------------------

def _safe_action(action: dict) -> str:
    """Return the full SQL as a single-line string (no truncation, no outer quotes)."""
    action_type = action.get("action_type", "execute")
    if action_type == "noop":
        return "noop"
    raw = (
        action.get("sql")
        or action.get("inspect_query")
        or action.get("action_type", "noop")
    )
    # Collapse all whitespace variants to spaces — guarantees a single output line
    return raw.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()


def _safe_error(error: str | None) -> str:
    """Return a single-line error string, or 'null'."""
    if not error:
        return "null"
    return error.replace("\n", " ").replace("\r", " ").strip()


# ---------------------------------------------------------------------------
# Agent loop for a single task
# ---------------------------------------------------------------------------

def run_task(
    host: str,
    task: dict,
) -> dict:
    task_id    = task["id"]
    max_steps  = task["max_steps"]
    time_limit = task["time_limit"]
    label      = task["label"]

    step_start       = time.time()
    action_label     = "starting..."
    total_reward     = 0.0
    prev_reward      = 0.0          # for computing per-step delta
    reward_breakdown = {}
    actions_taken    = []
    step_rewards     = []           # per-step delta rewards
    done             = False
    success          = False
    obs              = {}

    # ── Reset environment ────────────────────────────────────────────────────
    try:
        obs = api_reset(host, task_id)
        time.sleep(3)
    except Exception as e:
        # Emit spec-compliant lines even on reset failure
        print(f"[START] task={task_id} env={BENCHMARK} model={MODEL_NAME}", flush=True)
        print(f"[END] success=false steps=0 rewards=0.00", flush=True)
        return {
            "task_id": task_id, "label": label,
            "score": 0.0, "breakdown": {}, "steps_used": 0,
            "elapsed": 0.0, "success": False, "actions": [],
            "error": str(e),
        }

    # ── Episode start ─────────────────────────────────────────────────────────
    print(f"[START] task={task_id} env={BENCHMARK} model={MODEL_NAME}", flush=True)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    try:
        for step_num in range(max_steps):
            elapsed = time.time() - step_start

            # Time limit guard
            if elapsed > time_limit:
                break

            # Build observation — trim schema to focus tables only
            focus  = obs.get("focus_tables", [])
            schema = obs.get("current_schema", {})
            trimmed_schema = {t: v for t, v in schema.items() if t in focus} if focus else schema
            obs_trimmed = {**obs, "current_schema": trimmed_schema}
            messages.append({"role": "user", "content": json.dumps(obs_trimmed)})

            # ── LLM call ─────────────────────────────────────────────────────
            action = None
            try:
                resp = client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=512,
                )
                raw = resp.choices[0].message.content or ""
                try:
                    action = json.loads(raw)
                except Exception:
                    import re
                    m = re.search(r'\{.*\}', raw, re.DOTALL)
                    if m:
                        try:
                            action = json.loads(m.group())
                        except Exception:
                            logger.warning("Non-JSON LLM output at step %d: %s", step_num + 1, raw[:200])
                    if action is None:
                        logger.warning("Non-JSON LLM output at step %d: %s", step_num + 1, raw[:200])
                        action = {"action_type": "inspect", "inspect_query": "SELECT 1"}
            except Exception as e:
                # LLM failed — emit noop so the step is still logged cleanly
                err_msg = _safe_error(str(e)) or "llm_error"
                action = {"action_type": "noop", "_error": err_msg}

            # ── Determine action type ─────────────────────────────────────────
            action_type = action.get("action_type", "execute")

            actions_taken.append(action)
            messages.append({"role": "assistant", "content": json.dumps(action)})

            # Keep context window: system prompt + last 2 exchanges
            if len(messages) > 5:
                messages = [messages[0]] + messages[-4:]

            # ── Submit to environment ─────────────────────────────────────────
            try:
                result = api_step(host, action)
            except Exception as e:
                err_str = _safe_error(str(e))
                print(
                    f"[STEP] step={step_num + 1} action={_safe_action(action)} "
                    f"reward={total_reward:.2f} done=false "
                    f"error={err_str}",
                    flush=True,
                )
                time.sleep(RATE_LIMIT_SLEEP)
                continue

            # ── Extract step results ──────────────────────────────────────────
            obs          = result.get("observation", obs)
            total_reward = result.get("reward", 0.0)
            done         = result.get("done", False)
            info         = result.get("info", {})

            # Per-step delta (not cumulative)
            step_delta  = round(total_reward - prev_reward, 2)
            step_rewards.append(step_delta)
            prev_reward = total_reward

            # Grader breakdown (stderr only)
            grader = info.get("grader", {})
            reward_breakdown = {
                "total":          total_reward,
                "schema_match":   grader.get("schema_score", 0.0),
                "data_integrity": grader.get("data_score", 0.0),
                "fk_integrity":   grader.get("fk_score", 0.0),
                "efficiency":     grader.get("efficiency_score", 0.0),
            }

            # [STEP] stdout - spec-compliant
            # On noop (LLM error), surface LLM error; otherwise surface env error
            env_error   = info.get("error") if action_type != "noop" else None
            llm_error   = action.get("_error") if action_type == "noop" else None
            error_field = _safe_error(llm_error or env_error)
            print(
                f"[STEP] step={step_num + 1} action={_safe_action(action)} "
                f"reward={total_reward:.2f} done={'true' if done else 'false'} "
                f"error={error_field}",
                flush=True,
            )

            if done:
                break

            time.sleep(RATE_LIMIT_SLEEP)

    except Exception:
        pass  # [END] always emitted in finally

    finally:
        success         = done and total_reward >= SUCCESS_THRESHOLD
        all_rewards_str = ",".join(f"{r:.2f}" for r in step_rewards) or "0.00"

        # ── [END] stdout — always emitted, even on exception ─────────────────
        print(
            f"[END] success={'true' if success else 'false'} "
            f"steps={min(len(actions_taken), max_steps)} "
            f"rewards={all_rewards_str}",
            flush=True,
        )

    return {
        "task_id":    task_id,
        "label":      label,
        "score":      round(total_reward, 4),
        "breakdown":  reward_breakdown,
        "steps_used": min(len(actions_taken), max_steps),
        "elapsed":    round(time.time() - step_start, 1),
        "success":    success,
        "actions":    [a.get("sql") or a.get("inspect_query", "") for a in actions_taken],
    }


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def main() -> list[dict]:
    parser = argparse.ArgumentParser(description="MigrateEnv Baseline Agent")
    parser.add_argument("--host",   default=DEFAULT_HOST, help="MigrateEnv server URL")
    parser.add_argument(
        "--tasks", nargs="+",
        default=[t["id"] for t in TASKS],
        help="Task IDs to run (easy, medium, hard)",
    )
    parser.add_argument("--output", default=None, help="Save results as JSON file")
    args = parser.parse_args()

    selected_tasks = [t for t in TASKS if t["id"] in args.tasks]

    # Wait up to 60 s for the MigrateEnv server + DB to be ready
    MAX_WAIT = 60
    waited   = 0
    while waited < MAX_WAIT:
        try:
            health = api_health(args.host)
            if health:
                break
        except Exception:
            pass
        time.sleep(3)
        waited += 3
    else:
        # Server not ready — exit cleanly (no stdout noise)
        sys.exit(1)

    results: list[dict] = []
    for task in selected_tasks:
        result = run_task(args.host, task)
        results.append(result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    main()
