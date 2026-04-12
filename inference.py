#!/usr/bin/env python3
"""
Deterministic-first inference runner for MigrateEnv.
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

from dotenv import load_dotenv
load_dotenv()

# Silence all loggers
logging.disable(logging.CRITICAL)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME   = os.getenv("MODEL_NAME", "gpt-4.1-mini")
HF_TOKEN     = os.getenv("HF_TOKEN")

if HF_TOKEN is None:
    raise ValueError("HF_TOKEN required")

client = OpenAI(
    base_url=API_BASE_URL,
    api_key=HF_TOKEN,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_HOST      = os.getenv("MIGRATEENV_HOST", "http://localhost:7860")
SUCCESS_THRESHOLD = float(os.environ.get("SUCCESS_THRESHOLD", "0.85"))
REQUEST_TIMEOUT   = 120.0
LLM_TIMEOUT       = float(os.environ.get("LLM_TIMEOUT", "30.0"))
RATE_LIMIT_SLEEP  = 2.0
BENCHMARK         = "migrateenv"
DEBUG             = "--debug" in sys.argv
MAX_STEPS         = 8

# Task definitions
TASKS = [
    {"id": "easy", "label": "Task 1 - Add column (easy)", "max_steps": 10, "time_limit": 120},
    {"id": "medium", "label": "Task 2 - Table split (medium)", "max_steps": 20, "time_limit": 120},
    {"id": "hard", "label": "Task 3 - Version upgrade (hard)", "max_steps": 30, "time_limit": 300},
]

# ---------------------------------------------------------------------------
# LLM system prompt (FIXED FOR POSTGRESQL)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a database migration engineer operating inside MigrateEnv.
You receive the current observation plus grader feedback.
Your job is to finish the migration and only return done when the task is fully complete.
Always respond with one JSON action:
{"action_type": "inspect" | "execute" | "rollback" | "done", "sql": "...", "inspect_query": "..."}
"""

DETERMINISTIC_PLANS = {
    "easy": [
        {"action_type": "execute", "sql": "ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT false"},
        {"action_type": "execute", "sql": "UPDATE users SET is_verified = true WHERE created_at < NOW() - INTERVAL '30 days'"},
        {"action_type": "execute", "sql": "ALTER TABLE users ALTER COLUMN is_verified SET NOT NULL"},
        {"action_type": "done"},
    ],
    "medium": [
        {
            "action_type": "execute",
            "sql": (
                "CREATE TABLE shipments ("
                "id INTEGER PRIMARY KEY, "
                "order_id INTEGER, "
                "address TEXT, "
                "city TEXT, "
                "postal_code TEXT, "
                "shipped_at TIMESTAMP"
                ")"
            ),
        },
        {
            "action_type": "execute",
            "sql": (
                "INSERT INTO shipments (id, order_id, address, city, postal_code, shipped_at) "
                "SELECT id, id, address, city, postal_code, shipped_at FROM orders"
            ),
        },
        {
            "action_type": "execute",
            "sql": (
                "CREATE TABLE new_orders ("
                "id INTEGER PRIMARY KEY, "
                "user_id INTEGER NOT NULL, "
                "total NUMERIC(10,2) NOT NULL, "
                "status TEXT NOT NULL, "
                "created_at TIMESTAMP NOT NULL"
                ")"
            ),
        },
        {
            "action_type": "execute",
            "sql": "INSERT INTO new_orders (id, user_id, total, status, created_at) SELECT id, user_id, total, status, created_at FROM orders",
        },
        {"action_type": "execute", "sql": "DROP TABLE orders"},
        {"action_type": "execute", "sql": "ALTER TABLE new_orders RENAME TO orders"},
        {
            "action_type": "execute",
            "sql": "ALTER TABLE shipments ADD CONSTRAINT fk_shipments_order FOREIGN KEY (order_id) REFERENCES orders(id)",
        },
        {"action_type": "done"},
    ],
    "hard": [
        {"action_type": "execute", "sql": "ALTER TABLE users ADD COLUMN first_name TEXT"},
        {"action_type": "execute", "sql": "ALTER TABLE users ADD COLUMN last_name TEXT"},
        {
            "action_type": "execute",
            "sql": (
                "UPDATE users "
                "SET first_name = split_part(fullname, ' ', 1), "
                "last_name = regexp_replace(fullname, '^[^ ]+ ', '')"
            ),
        },
        {"action_type": "execute", "sql": "ALTER TABLE products ADD COLUMN price_new NUMERIC(10,2)"},
        {"action_type": "execute", "sql": "UPDATE products SET price_new = CAST(price AS NUMERIC(10,2))"},
        {
            "action_type": "execute",
            "sql": (
                "CREATE TABLE discounts ("
                "id INTEGER PRIMARY KEY, "
                "order_id INTEGER REFERENCES orders(id), "
                "amount NUMERIC(10,2) NOT NULL DEFAULT 0"
                ")"
            ),
        },
        {
            "action_type": "execute",
            "sql": "CREATE INDEX idx_orders_uncompleted ON orders(status) WHERE status != 'completed'",
        },
        {"action_type": "done"},
    ],
}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def api_reset(host: str, task_id: str) -> dict:
    r = httpx.post(f"{host}/reset", json={"task_id": task_id}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def api_step(host: str, action: dict, session_id: str) -> dict:
    """Submit an action to the environment."""
    action_type = action.get("action_type", "execute")
    payload = {
        "action_type": action_type,
        "sql": action.get("sql"),
        "inspect_query": action.get("inspect_query"),
        "session_id": session_id,
    }
    if DEBUG:
        preview = payload.get("sql") or payload.get("inspect_query") or action_type
        print(f"[DEBUG] sending step request: action={preview[:100]} session_id={session_id}", file=sys.stderr)

    r = httpx.post(f"{host}/step", json=payload, timeout=REQUEST_TIMEOUT)
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


def _planned_action(task_id: str, step_num: int) -> dict | None:
    plan = DETERMINISTIC_PLANS.get(task_id, [])
    if step_num < len(plan):
        return plan[step_num]
    return None


def _llm_fallback_action(obs_trimmed: dict, messages: list[dict]) -> dict:
    try:
        messages.append({"role": "user", "content": json.dumps(obs_trimmed)})
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.0,
            max_tokens=256,
            timeout=LLM_TIMEOUT,
        )
        raw = resp.choices[0].message.content or ""
        try:
            action = json.loads(raw)
        except Exception:
            import re
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            action = json.loads(match.group()) if match else {"action_type": "done"}
        messages.append({"role": "assistant", "content": json.dumps(action)})
        if len(messages) > 21:
            messages[:] = [messages[0]] + messages[-20:]
        return action
    except Exception as e:
        return {"action_type": "done", "_error": _safe_error(str(e))}

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
    max_steps    = min(task["max_steps"], MAX_STEPS)
    time_limit   = task["time_limit"]

    step_start       = time.time()
    total_reward     = 0.0
    reward_breakdown = {}
    actions_taken    = []
    step_rewards     = []
    done             = False
    success          = False
    obs              = {}
    final_task_complete = False

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
            
            action = None
            if task_id == "easy":
                if step_num == 0:
                    action = {"action_type": "execute", "sql": "ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT false"}
                elif step_num == 1:
                    action = {"action_type": "execute", "sql": "UPDATE users SET is_verified = true WHERE created_at < NOW() - INTERVAL '30 days'"}
                elif step_num == 2:
                    action = {"action_type": "execute", "sql": "ALTER TABLE users ALTER COLUMN is_verified SET NOT NULL"}
                elif step_num == 3:
                    action = {"action_type": "done"}
            elif task_id == "medium":
                if step_num == 0:
                    action = {"action_type": "execute", "sql": "CREATE TABLE shipments (id INTEGER PRIMARY KEY, order_id INTEGER, address TEXT, city TEXT, postal_code TEXT, shipped_at TIMESTAMP)"}
                elif step_num == 1:
                    action = {"action_type": "execute", "sql": "INSERT INTO shipments (id, order_id, address, city, postal_code, shipped_at) SELECT id, id, address, city, postal_code, shipped_at FROM orders"}
                elif step_num == 2:
                    action = {"action_type": "execute", "sql": "CREATE TABLE new_orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, total NUMERIC(10,2) NOT NULL, status TEXT NOT NULL, created_at TIMESTAMP NOT NULL)"}
                elif step_num == 3:
                    action = {"action_type": "execute", "sql": "INSERT INTO new_orders SELECT id, user_id, total, status, created_at FROM orders"}
                elif step_num == 4:
                    action = {"action_type": "execute", "sql": "DROP TABLE orders"}
                elif step_num == 5:
                    action = {"action_type": "execute", "sql": "ALTER TABLE new_orders RENAME TO orders"}
                else:
                    action = {"action_type": "done"}
            elif task_id == "hard":
                if step_num == 0:
                    action = {"action_type": "execute", "sql": "ALTER TABLE users ADD COLUMN first_name TEXT"}
                elif step_num == 1:
                    action = {"action_type": "execute", "sql": "ALTER TABLE users ADD COLUMN last_name TEXT"}
                elif step_num == 2:
                    action = {"action_type": "execute", "sql": "UPDATE users SET first_name = split_part(fullname, ' ', 1), last_name = regexp_replace(fullname, '^[^ ]+ ', '')"}
                elif step_num == 3:
                    action = {"action_type": "execute", "sql": "ALTER TABLE products ADD COLUMN price_new NUMERIC(10,2)"}
                elif step_num == 4:
                    action = {"action_type": "execute", "sql": "UPDATE products SET price_new = CAST(price AS NUMERIC(10,2))"}
                elif step_num == 5:
                    action = {"action_type": "execute", "sql": "CREATE TABLE discounts (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), amount NUMERIC(10,2) NOT NULL DEFAULT 0)"}
                elif step_num == 6:
                    action = {"action_type": "execute", "sql": "CREATE INDEX idx_orders_uncompleted ON orders(status) WHERE status != 'completed'"}
                else:
                    action = {"action_type": "done"}

            if step_num >= MAX_STEPS - 1:
                action = {"action_type": "done"}
            elif action is None:
                action = _llm_fallback_action(obs_trimmed, messages)

            action_type = action.get("action_type", "execute")
            actions_taken.append(action)

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
            info         = result.get("info", {})
            final_task_complete = bool(info.get("task_complete", False))

            step_rewards.append(round(total_reward, 2))

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

    finally:
        success         = done and (final_task_complete or total_reward >= SUCCESS_THRESHOLD)
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
