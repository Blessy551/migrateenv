import os
import json
import time
import requests
from openai import OpenAI
from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
    SpinnerColumn,
)
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)
MODEL = "llama-3.3-70b-versatile"
ENV_URL = os.getenv("ENV_URL", "http://localhost:8000")

TASKS = [
    {"id": "task_add_column",      "label": "Task 1 — Add column (easy)",          "max_steps": 10,  "time_limit": 60},
    {"id": "task_table_split",     "label": "Task 2 — Table split (medium)",        "max_steps": 20,  "time_limit": 120},
    {"id": "task_version_upgrade", "label": "Task 3 — Version upgrade (hard)",   "max_steps": 40,  "time_limit": 300},
]

SYSTEM_PROMPT = """You are a database migration engineer.
You will receive an observation with a schema_snapshot and a target_spec.
Your goal is to migrate the database to match target_spec without data loss.
Always respond with a single JSON action:
{"action_type": "inspect"|"execute"|"rollback"|"done", "sql": "...", "inspect_query": "..."}
Think step by step. Inspect before executing. Use rollback if an execute fails."""

console = Console()


def run_task(task: dict, progress: Progress, task_progress_id) -> dict:
    task_id = task["id"]
    max_steps = task["max_steps"]
    time_limit = task["time_limit"]

    obs = requests.post(f"{ENV_URL}/reset", json={"task_id": task_id}).json()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    total_reward = 0.0
    reward_breakdown = {}
    step_start = time.time()
    action_label = "starting..."

    for step_num in range(max_steps):
        elapsed = time.time() - step_start
        time_pct = int((elapsed / time_limit) * 100)

        progress.update(
            task_progress_id,
            completed=step_num,
            description=f"[bold cyan]{task['label']}[/] "
                        f"[dim]step {step_num+1}/{max_steps} · "
                        f"{elapsed:.0f}s/{time_limit}s · "
                        f"last: {action_label[:40]}[/]",
        )

        messages.append({"role": "user", "content": json.dumps(obs)})

        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                timeout=30,
            )
            action = json.loads(resp.choices[0].message.content)
        except Exception as e:
            action = {"action_type": "done"}
            action_label = f"[err] {e}"

        action_type = action.get("action_type", "?")
        if action_type == "execute":
            sql_preview = (action.get("sql") or "")[:50].replace("\n", " ")
            action_label = f"execute: {sql_preview}"
        elif action_type == "inspect":
            action_label = f"inspect: {(action.get('inspect_query') or '')[:50]}"
        elif action_type == "rollback":
            action_label = "rollback"
        elif action_type == "done":
            action_label = "done — finalizing score"
        else:
            action_label = action_type

        messages.append({"role": "assistant", "content": json.dumps(action)})

        result = requests.post(f"{ENV_URL}/step", json=action).json()
        total_reward = result["reward"]["total"]
        reward_breakdown = result["reward"]
        obs = result["observation"]

        time.sleep(2)  # Groq rate limit buffer

        if result["done"]:
            break

    progress.update(
        task_progress_id,
        completed=max_steps,
        description=f"[bold green]{task['label']}[/] [dim]done[/]",
    )

    return {
        "task_id": task_id,
        "label": task["label"],
        "score": total_reward,
        "breakdown": reward_breakdown,
        "steps_used": obs.get("step_number", max_steps),
        "elapsed": time.time() - step_start,
    }


def print_results(results: list[dict]):
    table = Table(
        title="MigrateEnv — Baseline Results",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold",
    )
    table.add_column("Task",            style="cyan",  min_width=38)
    table.add_column("Score",           style="bold",  justify="center")
    table.add_column("Schema",          justify="center")
    table.add_column("Integrity",       justify="center")
    table.add_column("Steps used",      justify="center")
    table.add_column("Time",            justify="right")

    for r in results:
        score = r["score"]
        score_color = "green" if score >= 0.7 else "yellow" if score >= 0.4 else "red"
        b = r.get("breakdown", {})
        table.add_row(
            r["label"],
            f"[{score_color}]{score:.3f}[/{score_color}]",
            f"{b.get('schema_match', 0):.2f}",
            f"{b.get('data_integrity', 0):.2f}",
            str(r.get("steps_used", "?")),
            f"{r['elapsed']:.0f}s",
        )

    console.print()
    console.print(table)
    avg = sum(r["score"] for r in results) / len(results)
    console.print(
        Panel(
            f"[bold]Average score:[/] [{'green' if avg >= 0.6 else 'yellow'}]{avg:.3f}[/]   "
            f"[dim]Model: {MODEL}[/]",
            expand=False,
        )
    )


def main():
    console.print(Panel(
        "[bold]MigrateEnv Baseline Runner[/]\n"
        f"[dim]ENV_URL: {ENV_URL}  |  Model: {MODEL}[/]\n"
        "[yellow]Task 3 (hard) can take up to 5 minutes — the bar below shows live progress.[/]",
        title="migrateenv",
        expand=False,
    ))

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
        for task in TASKS:
            tid = progress.add_task(
                description=f"[bold cyan]{task['label']}[/] [dim]waiting...[/]",
                total=task["max_steps"],
            )
            result = run_task(task, progress, tid)
            results.append(result)

    print_results(results)


if __name__ == "__main__":
    main()
