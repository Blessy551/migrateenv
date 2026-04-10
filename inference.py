#!/usr/bin/env python3
"""
MigrateEnv Inference Script (PostgreSQL-Aware)
============================
FIXED VERSION: Uses PostgreSQL syntax, better feedback loop.

Key improvements:
1. System prompt updated for PostgreSQL (not SQLite)
2. Feedback loop improved
3. Better error messages

Required environment variables:
    API_BASE_URL   LLM endpoint (default: https://api.openai.com/v1)
    MODEL_NAME     Model identifier (default: gpt-4-mini)
    HF_TOKEN       API key (mandatory — no default)

Usage:
    python inference_FIXED.py --host https://your-space.hf.space --debug
"""
from __future__ import annotations
import os
import sys
import time
import json
import argparse
import logging
import traceback

import httpx
from openai import OpenAI
from rich.console import Console

from dotenv import load_dotenv
load_dotenv()

# Silence all loggers
logging.disable(logging.CRITICAL)
logger = logging.getLogger(__name__)

# Summary table only
console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME   = os.getenv("MODEL_NAME", "gpt-4.1-mini")
HF_TOKEN     = os.getenv("HF_TOKEN")

if HF_TOKEN is None:
    raise ValueError("HF_TOKEN environment variable is required")

client = OpenAI(
    base_url=API_BASE_URL,
    api_key=HF_TOKEN,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_HOST      = os.getenv("MIGRATEENV_HOST", "http://localhost:8000")
SUCCESS_THRESHOLD = float(os.environ.get("SUCCESS_THRESHOLD", "0.75"))
REQUEST_TIMEOUT   = 120.0
LLM_TIMEOUT       = float(os.environ.get("LLM_TIMEOUT", "30.0"))
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
# LLM system prompt (FIXED FOR POSTGRESQL)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a PostgreSQL database migration engineer operating inside MigrateEnv (Supabase).

You will receive an observation containing:
  - task_description: what needs to be migrated
  - hint: suggested SQL steps
  - current_schema: the live database schema
  - row_counts: current row counts per table
  - grader_feedback: what's still missing (if any)

Your goal is to migrate the Northwind database to match the task requirements without data loss.

Always respond with a single JSON action using this exact format:
{"action_type": "inspect" | "execute" | "done", "sql": "...", "inspect_query": "..."}

CRITICAL RULES (PostgreSQL/Supabase):
- You are working with PostgreSQL (Supabase), NOT SQLite.
- PostgreSQL DOES support:
  - information_schema.tables, information_schema.columns
  - ALTER TABLE ... ADD CONSTRAINT
  - ALTER TABLE ... ADD CHECK
  - SERIAL (auto-incrementing)
  - CASCADE for foreign keys
  - Table names are case-sensitive (use "CamelCase" in double quotes if needed)

WHAT TO AVOID (these are SQLite features, NOT PostgreSQL):
- PRAGMA table_info() — does NOT exist in PostgreSQL
- sqlite_master — does NOT exist in PostgreSQL
- Use information_schema instead

CORRECT POSTGRESQL SYNTAX EXAMPLES:
  - List all tables: SELECT table_name FROM information_schema.tables WHERE table_schema='public'
  - List columns: SELECT column_name, data_type, is_nullable FROM information_schema.columns 
                   WHERE table_schema='public' AND table_name='orders'
  - Add column: ALTER TABLE orders ADD COLUMN order_status VARCHAR(20) NOT NULL DEFAULT 'pending'
  - Add constraint: ALTER TABLE orders ADD CONSTRAINT check_status CHECK (order_status IN ('pending', 'shipped', 'done'))

MANDATORY FIRST STEP:
- You MUST ALWAYS start by inspecting the database.
- First run: SELECT table_name FROM information_schema.tables WHERE table_schema='public'
- Then inspect columns for relevant tables

WORKFLOW:
1. "inspect": run SELECT queries to understand current schema
2. "execute": run DDL/DML (ALTER TABLE, CREATE TABLE, INSERT, UPDATE, etc.)
3. "done": signal migration complete — ONLY after grader_feedback shows 0% issues

Rules:
- One action per response. Pure JSON only — no explanation.
- If execute fails, read the error and fix it in the next step.
- If grader_feedback still shows missing items, keep executing more steps.
- Do NOT use TRUNCATE, DROP DATABASE, or DROP SCHEMA.
- Do NOT drop tables unless specifically required.
- Always verify data integrity before marking done.

UNDERSTANDING GRADER FEEDBACK:
- grader_feedback shows: "Schema incomplete (33% match) | Missing columns: order_status"
- This means you need MORE steps to complete the migration
- Keep iterating until the task requirement is met
- Only send "done" when no issues remain

STOPPING RULE:
- Do NOT send action_type="done" unless reward_summary shows total >= 0.75
- If steps_remaining > 0 and grader_feedback shows anything missing, keep executing
- One step is never enough — a full migration takes 3-8 steps minimum
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
    else:  # done / noop
        sql = "SELECT 1"
    r = httpx.post(f"{host}/step", json={"sql": sql}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def api_health(host: str) -> dict:
    r = httpx.get(f"{host}/health", timeout=10.0)
    r.raise_for_status()
    return r.json()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_action(action: dict) -> str:
    """Return SQL as single-line string."""
    action_type = action.get("action_type", "execute")
    if action_type == "noop":
        return "noop"
    raw = (
        action.get("sql")
        or action.get("inspect_query")
        or action.get("action_type", "noop")
    )
    return raw.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()

def _safe_error(error: str | None) -> str:
    """Return error as single-line string."""
    if not error:
        return "null"
    return error.replace("\n", " ").replace('"', "'").strip()[:100]

# ---------------------------------------------------------------------------
# Task runner
# ---------------------------------------------------------------------------

def run_task(host: str, task: dict) -> dict:
    """Run a single task."""
    task_id      = task["id"]
    label        = task["label"]
    max_steps    = task["max_steps"]
    time_limit   = task["time_limit"]

    step_start       = time.time()
    total_reward     = 0.0
    prev_reward      = 0.0
    reward_breakdown = {}
    actions_taken    = []
    step_rewards     = []
    done             = False
    success          = False
    obs              = {}

    # Reset environment
    try:
        obs = api_reset(host, task_id)
        time.sleep(3)
    except Exception as e:
        print(f"[START] task={task_id} env={BENCHMARK} model={MODEL_NAME}", flush=True)
        print(f"[END] success=false steps=0 rewards=0.00", flush=True)
        return {
            "task_id": task_id, "label": label,
            "score": 0.0, "breakdown": {}, "steps_used": 0,
            "elapsed": 0.0, "success": False, "actions": [],
            "error": str(e),
        }

    # Episode start
    print(f"[START] task={task_id} env={BENCHMARK} model={MODEL_NAME}", flush=True)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    last_feedback = ""
    last_reward_summary = ""

    try:
        for step_num in range(max_steps):
            elapsed = time.time() - step_start

            # Time limit guard
            if elapsed > time_limit:
                break

            # Build observation — include grader feedback!
            focus  = obs.get("focus_tables", [])
            schema = obs.get("current_schema", {})
            trimmed_schema = {t: v for t, v in schema.items() if t in focus} if focus else schema
            obs_trimmed = {**obs, "current_schema": trimmed_schema}
            
            # Include feedback so LLM knows what's missing
            obs_trimmed["grader_feedback"] = last_feedback
            obs_trimmed["reward_summary"] = last_reward_summary
            obs_trimmed["steps_remaining"] = max_steps - step_num
            
            messages.append({"role": "user", "content": json.dumps(obs_trimmed)})

            # LLM call
            action = None
            try:
                resp = client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=512,
                    timeout=LLM_TIMEOUT,
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
                err_msg = _safe_error(str(e)) or "llm_error"
                action = {"action_type": "noop", "_error": err_msg}

            action_type = action.get("action_type", "execute")
            actions_taken.append(action)
            messages.append({"role": "assistant", "content": json.dumps(action)})

            # Keep context window: system + last 20 exchanges
            if len(messages) > 21:
                messages = [messages[0]] + messages[-20:]

            # Submit to environment
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

            # Extract step results
            obs          = result.get("observation", obs)
            total_reward = result.get("reward", 0.0)
            done         = result.get("done", False)
            info         = result.get("info", {})

            # Per-step delta
            step_delta  = round(total_reward - prev_reward, 2)
            step_rewards.append(step_delta)
            prev_reward = total_reward

            # Grader breakdown
            grader = info.get("grader", {})
            last_feedback = grader.get("feedback", "") or grader.get("details", {}).get("schema", {}).get("feedback", "")
            last_reward_summary = (
                f"schema={grader.get('schema_score', 0):.2f} "
                f"data={grader.get('data_score', 0):.2f} "
                f"fk={grader.get('fk_score', 0):.2f} "
                f"total={total_reward:.2f}"
            )
            reward_breakdown = {
                "total":          total_reward,
                "schema_match":   grader.get("schema_score", 0.0),
                "data_integrity": grader.get("data_score", 0.0),
                "fk_integrity":   grader.get("fk_score", 0.0),
                "efficiency":     grader.get("efficiency_score", 0.0),
            }

            # [STEP] stdout
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
        pass

    finally:
        success         = done and total_reward >= SUCCESS_THRESHOLD
        all_rewards_str = ",".join(f"{r:.2f}" for r in step_rewards) or "0.00"

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
# Main
# ---------------------------------------------------------------------------

def main() -> list[dict]:
    parser = argparse.ArgumentParser(description="MigrateEnv Baseline Agent (PostgreSQL-aware)")
    parser.add_argument("--host",   default=DEFAULT_HOST, help="MigrateEnv server URL")
    parser.add_argument(
        "--tasks", nargs="+",
        default=[t["id"] for t in TASKS],
        help="Task IDs to run (easy, medium, hard)",
    )
    parser.add_argument("--output", default=None, help="Save results as JSON file")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    args = parser.parse_args()

    selected_tasks = [t for t in TASKS if t["id"] in args.tasks]

    if args.debug:
        print(f"[DEBUG] Host: {args.host}", file=sys.stderr)
        print(f"[DEBUG] Model: {MODEL_NAME}", file=sys.stderr)
        print(f"[DEBUG] LLM Timeout: {LLM_TIMEOUT}s", file=sys.stderr)
        print(f"[DEBUG] Tasks: {[t['id'] for t in selected_tasks]}", file=sys.stderr)
        print(f"[DEBUG] Starting health check...", file=sys.stderr)

    # Health check
    MAX_WAIT = 60
    waited   = 0
    last_error = None
    
    while waited < MAX_WAIT:
        try:
            if args.debug:
                print(f"[DEBUG] Health check attempt {waited//3 + 1}/20...", file=sys.stderr, end=" ")
            health = api_health(args.host)
            if health:
                if args.debug:
                    print(f"✓ Server is ready!", file=sys.stderr)
                break
        except Exception as e:
            last_error = str(e)
            if args.debug:
                print(f"✗ {type(e).__name__}", file=sys.stderr)
        time.sleep(3)
        waited += 3
    else:
        print(
            f"\n❌ ERROR: Could not connect to server at {args.host} after {MAX_WAIT}s\n"
            f"   Last error: {last_error}\n",
            file=sys.stderr
        )
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
