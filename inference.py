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
DEBUG             = "--debug" in sys.argv

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
SYSTEM_PROMPT = """You are a PostgreSQL database migration engineer operating inside MigrateEnv.

You will receive an observation containing:
  - task_id: 'easy', 'medium', or 'hard'
  - task_description: specific target for the migration
  - target_spec: technical requirements for the schema
  - current_schema: the live database schema
  - hint: suggested SQL steps
  - grader_feedback: what's still missing

TASK 1 (Easy): loyalty_tier Column
- Goal: Add `loyalty_tier` VARCHAR(20) DEFAULT 'standard' NOT NULL to `customers`.
- Constraint: Add CHECK constraint `chk_loyalty_tier` (standard, silver, gold, platinum).

TASK 2 (Medium): Product Pricing Split
- Goal: Move `unit_price`, `quantity_per_unit`, and `discontinued` from `products` to a new table `product_pricing`.
- product_pricing: (id, product_id FK, unit_price, quantity_per_unit, discontinued).

TASK 3 (Hard): Order Status Enum & Index
- Goal: Add `order_status` column to `orders` and a composite index.

WORKFLOW:
1. Always "inspect" first to see the current schema.
2. Formulate "execute" steps to move toward the target_spec.
3. Only send "done" when you believe the task is 100% complete and grader_feedback shows no issues.

Always respond with a single JSON action:
{"action_type": "inspect" | "execute" | "done", "sql": "...", "inspect_query": "..."}
"""

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def api_reset(host: str, task_id: str) -> dict:
    r = httpx.post(f"{host}/reset", json={"task_id": task_id}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def api_step(host: str, action: dict, session_id: str) -> dict:
    """Submit an action; maps action_type to the correct SQL field."""
    action_type = action.get("action_type", "execute")
    if action_type == "execute":
        sql = action.get("sql", "SELECT 1")
    elif action_type == "inspect":
        sql = action.get("inspect_query", "SELECT 1")
    else:  # done / noop
        sql = "SELECT 1"
        
    if DEBUG:
        print(f"[DEBUG] sending step request: sql={sql[:100]} session_id={session_id}", file=sys.stderr)
        
    r = httpx.post(f"{host}/step", json={"sql": sql, "session_id": session_id}, timeout=REQUEST_TIMEOUT)
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
        reset_resp = api_reset(host, task_id)
        session_id = reset_resp.get("session_id")
        obs = reset_resp.get("observation", reset_resp)
        
        if DEBUG:
            print(f"[DEBUG] session_id initialized: {session_id}", file=sys.stderr)
            
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
                print(f"[DEBUG] LLM raw output: {raw}", file=sys.stderr)
                try:
                    action = json.loads(raw)
                except Exception:
                    import re
                    m = re.search(r'\{.*\}', raw, re.DOTALL)
                    if m:
                        try:
                            action = json.loads(m.group())
                        except Exception:
                            action = {"action_type": "inspect", "inspect_query": "SELECT 1"}
                    else:
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
            action_sql = action.get("sql") or action.get("inspect_query") or ""

            if DEBUG:
                print(f"[DEBUG] step={step_num+1} using session_id={session_id}", file=sys.stderr)
                
            try:
                result = api_step(host, action, session_id)
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
            if total_reward >= 0.75:
                done = True
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

    except Exception as e:
        print(f"[DEBUG ERROR] {str(e)}", file=sys.stderr)
        import traceback
        traceback.print_exc()

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
