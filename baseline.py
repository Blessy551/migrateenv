#!/usr/bin/env python3
"""
MigrateEnv Baseline Agent (Combined)
=====================================
Merges the rich progress UI and JSON action protocol from baseline_1.py
with the CLI flags, httpx client, OpenAI fallback, grader score display,
and JSON output from baseline.py.

Uses Groq (llama-3.3-70b-versatile) or OpenAI to attempt all 3 migration tasks.

Usage:
    GROQ_API_KEY=your_key python baseline.py
    GROQ_API_KEY=your_key python baseline.py --host http://localhost:8000 --tasks easy medium --output results.json

The agent:
1. Resets the environment for each task (deterministic Northwind reload)
2. Reads the observation (schema snapshot + task description + hint)
3. Asks the LLM for the next action as JSON {action_type, sql, inspect_query}
4. Submits via POST /step
5. Repeats until done or max_steps / time_limit reached
6. Reports final scores in a rich formatted table
"""
from __future__ import annotations
import os
import sys
import time
import json
import argparse
import logging
from typing import Optional

import httpx
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
    SpinnerColumn,
)

logger = logging.getLogger(__name__)
console = Console()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_HOST = os.getenv("MIGRATEENV_HOST", "http://localhost:8000")

# Minimum score to mark a task as PASS — must match env.py SUCCESS_THRESHOLD
SUCCESS_THRESHOLD: float = float(os.environ.get("SUCCESS_THRESHOLD", "0.9"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.3-70b-versatile"   # from baseline_1.py (more capable)
OPENAI_MODEL = "gpt-4o-mini"

REQUEST_TIMEOUT = 120.0  # seconds per HTTP call
GROQ_RATE_LIMIT_SLEEP = 2.0  # seconds between steps (Groq free-tier buffer)

# Task definitions — merges baseline_1 labels/time_limits with baseline task IDs
TASKS = [
    {
        "id": "easy",
        "label": "Task 1 — Add column (easy)",
        "max_steps": 10,
        "time_limit": 60,
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
# LLM System Prompt  (JSON action protocol from baseline_1)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a PostgreSQL database migration engineer operating inside MigrateEnv.

You will receive an observation containing:
  - task_description: what needs to be migrated
  - hint: suggested SQL steps
  - current_schema: the live database schema
  - row_counts: current row counts per table

Your goal is to migrate the Northwind database to match the task requirements without data loss.

Always respond with a single JSON action using this exact format:
{"action_type": "inspect" | "execute" | "done", "sql": "...", "inspect_query": "..."}

Rules:
- "inspect": run a read-only SQL query (SELECT) to understand current state — set inspect_query
- "execute": run a migration SQL statement (ALTER TABLE, CREATE TABLE, UPDATE, CREATE INDEX, INSERT) — set sql
- "done": signal that the migration is complete
- One action per response. No explanation. Pure JSON only.
- Use standard PostgreSQL syntax.
- Do NOT use TRUNCATE, DROP DATABASE, or DROP SCHEMA.
- Think step by step: inspect first, then execute.
- If an execute fails, try a corrected version next turn.
"""

# ---------------------------------------------------------------------------
# LLM client setup
# ---------------------------------------------------------------------------

def get_llm_client() -> tuple[OpenAI, str]:
    """Returns (client, model_name) using GROQ_API_KEY or OPENAI_API_KEY."""
    if GROQ_API_KEY:
        console.print("[green]Using Groq API[/green]")
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
        return client, GROQ_MODEL
    elif OPENAI_API_KEY:
        console.print("[yellow]Using OpenAI API[/yellow]")
        client = OpenAI(api_key=OPENAI_API_KEY)
        return client, OPENAI_MODEL
    else:
        console.print("[red]ERROR[/red]: No API key found. Set GROQ_API_KEY or OPENAI_API_KEY.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# HTTP helpers (httpx from baseline.py)
# ---------------------------------------------------------------------------

def api_reset(host: str, task_id: str) -> dict:
    r = httpx.post(f"{host}/reset", json={"task_id": task_id}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def api_step(host: str, action: dict) -> dict:
    """
    Sends an action dict to POST /step.
    Extracts the SQL from action_type:
      - execute → sends action["sql"]
      - inspect → sends action["inspect_query"] as a read SQL
      - done    → sends SELECT 1 (no-op to get final grading)
    """
    action_type = action.get("action_type", "execute")
    if action_type == "execute":
        sql = action.get("sql", "SELECT 1")
    elif action_type == "inspect":
        sql = action.get("inspect_query", "SELECT 1")
    else:  # done
        sql = "SELECT 1"

    r = httpx.post(f"{host}/step", json={"sql": sql}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def api_health(host: str) -> dict:
    r = httpx.get(f"{host}/health", timeout=10.0)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Agent loop for a single task (merged best of both)
# ---------------------------------------------------------------------------

def run_task(
    host: str,
    task: dict,
    client: OpenAI,
    model: str,
    progress: Progress,
    task_progress_id,
) -> dict:
    task_id = task["id"]
    max_steps = task["max_steps"]
    time_limit = task["time_limit"]
    label = task["label"]

    step_start = time.time()
    action_label = "starting..."

    # Reset environment
    try:
        obs = api_reset(host, task_id)
    except Exception as e:
        console.print(f"  [red]Reset failed for '{task_id}':[/red] {e}")
        progress.update(task_progress_id, completed=max_steps,
                        description=f"[red]{label}[/] [dim]reset failed[/]")
        return {
            "task_id": task_id,
            "label": label,
            "score": 0.0,
            "breakdown": {},
            "steps_used": 0,
            "elapsed": 0.0,
            "success": False,
            "actions": [],
            "error": str(e),
        }

    difficulty = obs.get("difficulty", "?")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    total_reward = 0.0
    reward_breakdown = {}
    actions_taken = []
    done = False

    for step_num in range(max_steps):
        elapsed = time.time() - step_start

        # Time limit guard
        if elapsed > time_limit:
            console.print(f"  [yellow]Time limit ({time_limit}s) reached at step {step_num}[/yellow]")
            break

        # Update rich progress bar
        progress.update(
            task_progress_id,
            completed=step_num,
            description=(
                f"[bold cyan]{label}[/] "
                f"[dim]step {step_num + 1}/{max_steps} · "
                f"{elapsed:.0f}s/{time_limit}s · "
                f"last: {action_label[:45]}[/]"
            ),
        )

        # Build message from observation
        obs_trimmed = {k: v for k, v in obs.items() if k != "current_schema"}
        messages.append({"role": "user", "content": json.dumps(obs_trimmed)})

        # Ask LLM for next action
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=512,
                timeout=30,
            )
            action = json.loads(resp.choices[0].message.content)
        except Exception as e:
            logger.warning(f"LLM error at step {step_num + 1}: {e}")
            action = {"action_type": "done"}
            action_label = f"[llm-err] {str(e)[:40]}"

        # Parse action type for display
        action_type = action.get("action_type", "execute")
        if action_type == "execute":
            raw_sql = (action.get("sql") or "")
            sql_preview = raw_sql[:100].replace("\n", " ")
            if len(raw_sql) > 100:
                sql_preview += "..."
            action_label = sql_preview
            action_type_display = "EXECUTE"
        elif action_type == "inspect":
            raw_iq = (action.get('inspect_query') or '')
            iq_preview = raw_iq[:100].replace("\n", " ")
            if len(raw_iq) > 100:
                iq_preview += "..."
            action_label = iq_preview
            action_type_display = "INSPECT"
        elif action_type == "done":
            action_label = "(finalizing score)"
            action_type_display = "DONE"
        else:
            action_label = str(action_type)
            action_type_display = action_type.upper()

        actions_taken.append(action)
        messages.append({"role": "assistant", "content": json.dumps(action)})

        # Submit to environment
        try:
            result = api_step(host, action)
        except Exception as e:
            console.print(f"  [red]Step API error at step {step_num + 1}:[/red] {e}")
            time.sleep(GROQ_RATE_LIMIT_SLEEP)
            continue

        obs = result.get("observation", obs)
        total_reward = result.get("reward", 0.0)
        done = result.get("done", False)
        info = result.get("info", {})

        # Extract grader breakdown
        grader = info.get("grader", {})
        reward_breakdown = {
            "total": total_reward,
            "schema_match": grader.get("schema_score", 0.0),
            "data_integrity": grader.get("data_score", 0.0),
            "fk_integrity": grader.get("fk_score", 0.0),
            "efficiency": grader.get("efficiency_score", 0.0),
        }

        # Per-step grader output
        feedback = grader.get("feedback", "")
        console.print("\n[STEP]")
        console.print(f"Type:    {action_type_display}")
        console.print(f"SQL:     {action_label[:110]}")
        console.print(
            f"Reward:  {total_reward:.4f} | "
            f"Schema: {grader.get('schema_score', '?')} | "
            f"Data: {grader.get('data_score', '?')} | "
            f"FK: {grader.get('fk_score', '?')} | "
            f"Done: {done}"
        )
        if feedback and feedback != "Schema looks correct":
            console.print("Feedback:")
            for block in feedback.split("\n"):
                block = block.strip()
                if block:
                    console.print(f"  {block}")

        if done or action_type == "done":
            console.print("\nTask complete.")
            break

        time.sleep(GROQ_RATE_LIMIT_SLEEP)

    progress.update(
        task_progress_id,
        completed=max_steps,
        description=f"[bold green]{label}[/] [dim]done[/]",
    )

    # success = task completed (done) AND score met the threshold
    success = done and total_reward >= SUCCESS_THRESHOLD

    return {
        "task_id": task_id,
        "label": label,
        "score": round(total_reward, 4),
        "breakdown": reward_breakdown,
        "steps_used": min(len(actions_taken), max_steps),
        "elapsed": round(time.time() - step_start, 1),
        "success": success,
        "difficulty": difficulty,
        "actions": [a.get("sql") or a.get("inspect_query", "") for a in actions_taken],
    }


# ---------------------------------------------------------------------------
# Results display (rich table from baseline_1 + extra columns from baseline)
# ---------------------------------------------------------------------------

def print_results(results: list[dict], model: str):
    table = Table(
        title="MigrateEnv - Baseline Results",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
        title_style="bold",
    )
    table.add_column("Task",    style="cyan", min_width=38)
    table.add_column("Score",   style="bold", justify="center")
    table.add_column("Schema",  justify="center")
    table.add_column("Data",    justify="center")
    table.add_column("FK",      justify="center")
    table.add_column("Steps",   justify="center")
    table.add_column("Time",    justify="right")
    table.add_column("Result",  justify="center")

    for r in results:
        score = r["score"]
        score_color = "green" if score >= 0.7 else "yellow" if score >= 0.4 else "red"
        b = r.get("breakdown", {})
        result_str = "[green]PASS[/green]" if r.get("success") else "[red]FAIL[/red]"
        table.add_row(
            r["label"],
            f"[{score_color}]{score:.4f}[/{score_color}]",
            f"{b.get('schema_match', 0):.3f}",
            f"{b.get('data_integrity', 0):.3f}",
            f"{b.get('fk_integrity', 0):.3f}",
            str(r.get("steps_used", "?")),
            f"{r.get('elapsed', 0):.0f}s",
            result_str,
        )

    console.print()
    console.print(table)

    avg = sum(r["score"] for r in results) / len(results) if results else 0.0
    passed = sum(1 for r in results if r.get("success"))
    console.print(f"Average Score: {avg:.4f}")
    console.print(f"Tasks Passed:  {passed}/{len(results)}")


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MigrateEnv Baseline Agent")
    parser.add_argument("--host", default=DEFAULT_HOST, help="MigrateEnv server URL")
    parser.add_argument(
        "--tasks", nargs="+",
        default=[t["id"] for t in TASKS],
        help="Task IDs to run (easy, medium, hard)",
    )
    parser.add_argument("--output", default=None, help="Save results as JSON file")
    args = parser.parse_args()

    selected_tasks = [t for t in TASKS if t["id"] in args.tasks]

    task_ids = ", ".join(t["id"] for t in selected_tasks)
    console.print("\n=== MigrateEnv Baseline Runner ===")
    console.print(f"ENV_URL: {args.host}  |  Tasks: {task_ids}")
    console.print("Note: Hard task can take up to 5 minutes.")

    # Health check
    try:
        health = api_health(args.host)
        db_ok = health.get("database_connected", False)
        console.print(
            f"Health: [green]OK[/green] | "
            f"DB: {'[green]Connected[/green]' if db_ok else '[red]NOT connected[/red]'}"
        )
    except Exception as e:
        console.print(f"[red]Cannot reach server at {args.host}: {e}[/red]")
        console.print("Start the server first:  uvicorn app.main:app --reload")
        sys.exit(1)

    client, model = get_llm_client()
    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        for task in selected_tasks:
            tid = progress.add_task(
                description=f"[bold cyan]{task['label']}[/] [dim]waiting...[/]",
                total=task["max_steps"],
            )
            result = run_task(args.host, task, client, model, progress, tid)
            results.append(result)
            console.print()

    print_results(results, model)

    # Optional JSON output
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        console.print(f"\n[dim]Results saved to: {args.output}[/dim]")

    return results


if __name__ == "__main__":
    main()
