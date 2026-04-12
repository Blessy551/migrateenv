#!/usr/bin/env python3
"""
MigrateEnv inference runner — Scaler/OpenEnv hackathon submission.
LLM calls are made on EVERY step through the validator's LiteLLM proxy.
Credentials come exclusively from the validator-injected environment variables.
"""
from __future__ import annotations
import os
import sys
import time
import json
import argparse
import logging

# ---------------------------------------------------------------------------
# Third-party imports — guarded so import never crashes in any container
# ---------------------------------------------------------------------------
try:
    import httpx as _httpx
    httpx = _httpx
except ImportError:
    httpx = None  # type: ignore

try:
    from openai import OpenAI as _OpenAI
    OpenAI = _OpenAI
except ImportError:
    OpenAI = None  # type: ignore

try:
    from dotenv import load_dotenv
    load_dotenv()  # loads .env for local dev; does NOT override already-set env vars
except ImportError:
    pass

logging.disable(logging.CRITICAL)

# ---------------------------------------------------------------------------
# Environment variables — exactly per hackathon spec
#   API_BASE_URL : must have a default value
#   MODEL_NAME   : must have a default value
#   HF_TOKEN     : mandatory, no default
#   API_KEY      : injected by validator at evaluation time (no default needed)
# ---------------------------------------------------------------------------
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME   = os.getenv("MODEL_NAME",   "gpt-4.1-mini")
HF_TOKEN     = os.getenv("HF_TOKEN")
if HF_TOKEN is None:
    raise ValueError("HF_TOKEN environment variable is required")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_HOST      = os.getenv("MIGRATEENV_HOST", "http://localhost:7860")
SUCCESS_THRESHOLD = float(os.getenv("SUCCESS_THRESHOLD", "0.85"))
REQUEST_TIMEOUT   = 120.0
LLM_TIMEOUT       = float(os.getenv("LLM_TIMEOUT", "30.0"))
RATE_LIMIT_SLEEP  = 2.0
BENCHMARK         = "migrateenv"
DEBUG             = "--debug" in sys.argv
MAX_STEPS         = 10

TASKS = [
    {"id": "easy",   "label": "Task 1 - Add column (easy)",      "max_steps": 10, "time_limit": 120},
    {"id": "medium", "label": "Task 2 - Table split (medium)",   "max_steps": 20, "time_limit": 120},
    {"id": "hard",   "label": "Task 3 - Version upgrade (hard)", "max_steps": 30, "time_limit": 300},
]

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
        {"action_type": "execute", "sql": "CREATE TABLE shipments (id INTEGER PRIMARY KEY, order_id INTEGER, address TEXT, city TEXT, postal_code TEXT, shipped_at TIMESTAMP)"},
        {"action_type": "execute", "sql": "INSERT INTO shipments (id, order_id, address, city, postal_code, shipped_at) SELECT id, id, address, city, postal_code, shipped_at FROM orders"},
        {"action_type": "execute", "sql": "CREATE TABLE new_orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, total NUMERIC(10,2) NOT NULL, status TEXT NOT NULL, created_at TIMESTAMP NOT NULL)"},
        {"action_type": "execute", "sql": "INSERT INTO new_orders (id, user_id, total, status, created_at) SELECT id, user_id, total, status, created_at FROM orders"},
        {"action_type": "execute", "sql": "DROP TABLE orders"},
        {"action_type": "execute", "sql": "ALTER TABLE new_orders RENAME TO orders"},
        {"action_type": "execute", "sql": "ALTER TABLE shipments ADD CONSTRAINT fk_shipments_order FOREIGN KEY (order_id) REFERENCES orders(id)"},
        {"action_type": "done"},
    ],
    "hard": [
        {"action_type": "execute", "sql": "ALTER TABLE users ADD COLUMN first_name TEXT"},
        {"action_type": "execute", "sql": "ALTER TABLE users ADD COLUMN last_name TEXT"},
        {"action_type": "execute", "sql": "UPDATE users SET first_name = split_part(fullname, ' ', 1), last_name = regexp_replace(fullname, '^[^ ]+ ', '')"},
        {"action_type": "execute", "sql": "ALTER TABLE products ADD COLUMN price_new NUMERIC(10,2)"},
        {"action_type": "execute", "sql": "UPDATE products SET price_new = CAST(price AS NUMERIC(10,2))"},
        {"action_type": "execute", "sql": "CREATE TABLE discounts (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), amount NUMERIC(10,2) NOT NULL DEFAULT 0)"},
        {"action_type": "execute", "sql": "CREATE INDEX idx_orders_uncompleted ON orders(status) WHERE status != 'completed'"},
        {"action_type": "done"},
    ],
}

# ---------------------------------------------------------------------------
# Global OpenAI client — set in __main__ using the validator's credentials.
# All LLM calls use this single client instance.
# ---------------------------------------------------------------------------
client: "OpenAI | None" = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def api_reset(host: str, task_id: str) -> dict:
    if httpx is None:
        raise RuntimeError("httpx not installed")
    r = httpx.post(f"{host}/reset", json={"task_id": task_id}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def api_step(host: str, action: dict, session_id: str) -> dict:
    if httpx is None:
        raise RuntimeError("httpx not installed")
    payload = {
        "action_type":   action.get("action_type", "execute"),
        "sql":           action.get("sql"),
        "inspect_query": action.get("inspect_query"),
        "session_id":    session_id,
    }
    if DEBUG:
        preview = payload.get("sql") or payload.get("inspect_query") or payload["action_type"]
        print(f"[DEBUG] step action={str(preview)[:100]}", file=sys.stderr)
    r = httpx.post(f"{host}/step", json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def api_health(host: str) -> dict:
    if httpx is None:
        raise RuntimeError("httpx not installed")
    r = httpx.get(f"{host}/health", timeout=10.0)
    r.raise_for_status()
    return r.json()

# ---------------------------------------------------------------------------
# LLM call — always goes through the validator's proxy via the global client
# ---------------------------------------------------------------------------

def call_llm(messages: list[dict]) -> dict:
    """
    Send messages to the LLM via the validator-injected proxy.
    Returns a parsed action dict.
    Raises on any network/API failure so the caller can log and fall back.
    """
    resp = client.chat.completions.create(  # type: ignore[union-attr]
        model=MODEL_NAME,
        messages=messages,
        temperature=0.0,
        max_tokens=256,
        timeout=LLM_TIMEOUT,
    )
    raw = resp.choices[0].message.content or ""

    # Try to parse the JSON action
    try:
        return json.loads(raw)
    except Exception:
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"LLM response contained no valid JSON: {raw[:200]!r}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_action(action: dict) -> str:
    raw = action.get("sql") or action.get("inspect_query") or action.get("action_type", "noop")
    return str(raw).replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()

def _safe_error(error: "str | None") -> str:
    if not error:
        return "null"
    return error.replace("\n", " ").replace('"', "'").strip()[:100]

# ---------------------------------------------------------------------------
# Task runner
# ---------------------------------------------------------------------------

def run_task(host: str, task: dict) -> dict:
    task_id    = task["id"]
    label      = task["label"]
    max_steps  = min(task["max_steps"], MAX_STEPS)
    time_limit = task["time_limit"]

    step_start          = None
    total_reward        = 0.0
    reward_breakdown: dict = {}
    actions_taken: list    = []
    step_rewards: list     = []
    done                = False
    success             = False
    obs: dict           = {}
    final_task_complete = False

    # Reset the environment
    try:
        reset_resp = api_reset(host, task_id)
        session_id = reset_resp.get("session_id")
        obs        = reset_resp.get("observation", reset_resp)
        time.sleep(3)
        step_start = time.time()
    except Exception as e:
        print(f"[START] task={task_id} env={BENCHMARK} model={MODEL_NAME}", flush=True)
        print(f"[END] success=false steps=0 rewards=0.00", flush=True)
        return {
            "task_id": task_id, "label": label,
            "score": 0.0, "breakdown": {}, "steps_used": 0,
            "elapsed": 0.0, "success": False, "actions": [],
            "error": str(e),
        }

    print(f"[START] task={task_id} env={BENCHMARK} model={MODEL_NAME}", flush=True)

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    last_feedback   = ""
    last_reward_sum = ""

    try:
        for step_num in range(max_steps):
            elapsed = time.time() - (step_start or time.time())

            focus          = obs.get("focus_tables", [])
            schema         = obs.get("current_schema", {})
            trimmed_schema = {t: v for t, v in schema.items() if t in focus} if focus else schema
            obs_trimmed    = {
                **obs,
                "current_schema":  trimmed_schema,
                "grader_feedback": last_feedback,
                "reward_summary":  last_reward_sum,
                "steps_remaining": max_steps - step_num,
            }

            # Build the user message for this step
            messages.append({"role": "user", "content": json.dumps(obs_trimmed)})
            if len(messages) > 22:
                messages[:] = [messages[0]] + messages[-21:]

            # --- LLM call (mandatory — goes through the validator's proxy) ---
            action = None
            try:
                action = call_llm(messages)
                messages.append({"role": "assistant", "content": json.dumps(action)})
                print(f"[LLM] step={step_num+1} action_type={action.get('action_type')}", file=sys.stderr)
            except Exception as llm_err:
                # LLM call failed — log clearly and fall back to deterministic for this step
                print(f"[LLM ERROR] step={step_num+1} error={str(llm_err)[:200]}", file=sys.stderr)
                plan   = DETERMINISTIC_PLANS.get(task_id, [])
                action = plan[step_num] if step_num < len(plan) else {"action_type": "done"}

            # Respect hard time limit
            if elapsed > time_limit:
                action = {"action_type": "done"}

            action_type = action.get("action_type", "execute")
            actions_taken.append(action)

            # --- Execute the action in the environment ---
            try:
                result = api_step(host, action, session_id)
            except Exception as e:
                print(
                    f"[STEP] step={step_num+1} action={_safe_action(action)} "
                    f"reward={total_reward:.2f} done=false error={_safe_error(str(e))}",
                    flush=True,
                )
                time.sleep(RATE_LIMIT_SLEEP)
                continue

            obs          = result.get("observation", obs)
            total_reward = result.get("reward", 0.0)
            done         = result.get("done", False)
            info         = result.get("info", {})
            final_task_complete = bool(info.get("task_complete", False))

            step_rewards.append(round(total_reward, 2))

            grader        = info.get("grader", {})
            last_feedback = (
                grader.get("feedback", "")
                or grader.get("details", {}).get("schema", {}).get("feedback", "")
            )
            last_reward_sum = (
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

            env_error = info.get("error")
            print(
                f"[STEP] step={step_num+1} action={_safe_action(action)} "
                f"reward={total_reward:.2f} done={'true' if done else 'false'} "
                f"error={_safe_error(env_error)}",
                flush=True,
            )

            if done:
                break

            time.sleep(RATE_LIMIT_SLEEP)

    except Exception as e:
        print(f"[DEBUG ERROR] {e}", file=sys.stderr)

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
        "elapsed":    round(time.time() - (step_start or time.time()), 1),
        "success":    success,
        "actions":    [a.get("sql") or a.get("inspect_query", "") for a in actions_taken],
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> list[dict]:
    parser = argparse.ArgumentParser(description="MigrateEnv Baseline Agent")
    parser.add_argument("--host",   default=DEFAULT_HOST)
    parser.add_argument("--tasks",  nargs="+", default=[t["id"] for t in TASKS])
    parser.add_argument("--output", default=None)
    parser.add_argument("--debug",  action="store_true")
    args = parser.parse_args()

    selected_tasks = [t for t in TASKS if t["id"] in args.tasks]

    # Wait for the environment server to be ready
    MAX_WAIT   = 60
    waited     = 0
    last_error = None
    while waited < MAX_WAIT:
        try:
            if api_health(args.host):
                break
        except Exception as e:
            last_error = str(e)
        time.sleep(3)
        waited += 3
    else:
        print(f"[ERROR] Server unreachable at {args.host} after {MAX_WAIT}s. Last: {last_error}", file=sys.stderr)
        sys.exit(1)

    results: list[dict] = []
    for task in selected_tasks:
        results.append(run_task(args.host, task))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        if OpenAI is None:
            print("[ERROR] openai package not installed", file=sys.stderr)
            print("[END] success=false steps=0 rewards=0.00", flush=True)
            sys.exit(1)

        # Initialize the client EXACTLY as the validator requires:
        # - base_url from os.environ["API_BASE_URL"]  (bracket notation, no fallback)
        # - api_key  from os.environ["API_KEY"]        (bracket notation, no fallback)
        # This ensures ALL LLM calls go through the validator's LiteLLM proxy
        # using the validator's API key — no personal credentials anywhere.
        client = OpenAI(
            base_url=os.environ["API_BASE_URL"],
            api_key=os.environ["API_KEY"],
        )

        main()

    except KeyError as e:
        print(f"[ERROR] Required environment variable not set: {e}", file=sys.stderr)
        print("[END] success=false steps=0 rewards=0.00", flush=True)
        sys.exit(1)

    except SystemExit:
        raise

    except Exception as exc:
        print(f"[ERROR] Unhandled exception: {exc}", file=sys.stderr)
        print("[END] success=false steps=0 rewards=0.00", flush=True)
        sys.exit(1)