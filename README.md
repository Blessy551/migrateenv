---
title: MigrateEnv
emoji: "🛠️"
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
tags:
  - openenv
---

# MigrateEnv

MigrateEnv is a real-world SQL migration environment for evaluating agent behavior on schema evolution tasks. An agent acts as a database migration engineer: given a legacy schema and a target specification, it must inspect the live database, devise a migration plan, execute it safely, and preserve full data integrity — finishing the episode with an explicit `done` action.

## Motivation

Schema migration is one of the most failure-prone tasks in real software engineering. Migrations routinely cause data loss, downtime, and regressions — and are almost never taught formally. No existing OpenEnv environment covers this domain.

MigrateEnv provides a deterministic, containerized sandbox where agents can learn and be evaluated on exactly this skill. The graders are zero-ambiguity: the schema either matches the spec or it doesn't; the data is either intact or it isn't. Tasks span realistic difficulty levels, from adding a single backfilled column all the way to a zero-downtime multi-table version upgrade.

## Action Space

The environment accepts structured JSON actions via `POST /step`:

| Action type | Description |
|---|---|
| `inspect` | Ask the server for current schema and row-count context (processed by `SchemaInspector`, not raw SQL) |
| `execute` | Run a single SQL statement against the live database |
| `rollback` | Roll back the last `execute` if inside a transaction; grants a rollback bonus |
| `done` | Trigger final grading and end the episode |

Request body:

```json
{
  "session_id": "uuid",
  "action_type": "execute",
  "sql": "ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT false"
}
```

Only `execute` runs SQL. All statements pass through a sanitizer that blocks `DROP DATABASE`, `TRUNCATE` without `WHERE`, and any DDL outside the task's allowed table set.

## Observation Space

Each observation returned by `/reset` and `/step` includes:

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Identifier for the active task |
| `task_description` | string | Human-readable task summary |
| `difficulty` | string | `easy`, `medium`, or `hard` |
| `hint` | string | Optional directional hint |
| `target_spec` | object | The schema state the agent must reach |
| `current_schema` | object | Live snapshot of all tables, columns, and indexes |
| `row_counts` | object | Row count per table |
| `step_number` | int | Current step index |
| `max_steps` | int | Step budget for this task |
| `target_description` | string | Plain-language description of target state |
| `last_action_result` | string | Success/failure message from the previous action |
| `focus_tables` | array | Tables most relevant to the current task |

## Tasks

### Easy — Add Column (`easy`)

**Scenario:** A `users` table exists with `id`, `email`, and `created_at`. The target spec requires a new `is_verified` boolean column defaulting to `false`, plus a backfill: any user created more than 30 days ago must have `is_verified = true`.

**Starting state:** `users` table with 50 seed rows, varying `created_at` values.

**Target spec:**
```json
{
  "users": {
    "columns": ["id", "email", "created_at", "is_verified"],
    "is_verified": {"type": "BOOLEAN", "default": false, "not_null": true}
  }
}
```

**Grader checks:**
- Column exists with correct type and default → 0.4
- All 50 rows have `is_verified` set (not NULL) → 0.3
- Rows with `created_at` older than 30 days have `is_verified = true` → 0.3

**Max steps:** 10 | **Time limit:** 120s

---

### Medium — Table Split (`medium`)

**Scenario:** A monolithic `orders` table contains both order metadata and shipping details mixed together. The target spec splits this into a clean `orders` table (metadata only) and a new `shipments` table (shipping details with a foreign key back to `orders`).

**Starting state:** `orders` table with 200 rows, all columns mixed together.

**Target spec:**
```json
{
  "orders": {"columns": ["id", "user_id", "total", "status", "created_at"]},
  "shipments": {
    "columns": ["id", "order_id", "address", "city", "postal_code", "shipped_at"],
    "foreign_keys": [{"column": "order_id", "references": "orders.id"}]
  }
}
```

**Grader checks:**
- `orders` table schema matches spec → 0.25
- `shipments` table schema matches spec with FK → 0.25
- All 200 rows present in `orders`, zero data loss → 0.25
- All 200 corresponding rows present in `shipments` with correct `order_id` → 0.25

**Max steps:** 20 | **Time limit:** 120s

---

### Hard — Version Upgrade (`hard`)

**Scenario:** A v1 commerce schema (tables: `users`, `products`, `orders`) must be migrated to a v3 spec. Changes include splitting `users.fullname` into `first_name` + `last_name`, coercing `products.price` from TEXT to `NUMERIC(10,2)`, adding a `discounts` table with a FK to `orders`, and creating a partial index on `orders(status)` for unfinished orders — all while preserving every seeded row.

**Starting state:** v1 schema with seed rows across all tables.

**Grader checks:**
- Schema diff score (each correct column/table/index earns partial credit) → 0.35
- Data integrity: row counts preserved across all tables → 0.25
- `first_name`/`last_name` correctly split from `fullname` → 0.15
- `price` coercion valid (no NULL, no truncation) → 0.10
- Partial index present and correct → 0.10
- Rollback bonus (clean `BEGIN`/`SAVEPOINT` + rollback at least once) → +0.05

**Max steps:** 30 | **Time limit:** 300s

---

## Reward Function

A composite reward `R` in the range `[0.0, 1.0]` is returned at every `step()` call, not just at episode end:

```
R_total = (
    0.45 × schema_match_score    # primary objective
  + 0.25 × data_integrity_score  # row-level preservation
  - 0.15 × step_penalty          # (steps_used / max_steps) ^ 0.5
  - 0.10 × time_penalty          # applied when elapsed > 80% of time limit
  + 0.05 × rollback_bonus        # binary: used BEGIN + ROLLBACK correctly
)
```

Intermediate rewards are issued every step by diffing the current schema against the target spec after each `execute` — giving the agent a gradient to follow throughout the episode rather than only at termination.

**Penalized behaviors:**
- Executing on columns that are already correct → small negative signal
- More than 5 `inspect` calls without any `execute` → step penalty applied
- Calling `done` before the target spec is matched → episode ends with potentially low score

## Setup

**Prerequisites:** Docker, a PostgreSQL 15 instance (Supabase or self-hosted), and a `DATABASE_URL` connection string.

1. Copy the example environment file and fill in your database URL:
```bash
cp .env.example .env
# Set DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

2. Build and run the Docker container:
```bash
docker build -t migrateenv .
docker run --env-file .env -p 7860:7860 migrateenv
```

3. For local development without Docker:
```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 7860
```

## Usage

**Reset an episode:**
```bash
curl -X POST http://localhost:7860/reset \
  -H "Content-Type: application/json" \
  -d '{"task_id": "easy"}'
```

**Submit a step:**
```bash
curl -X POST http://localhost:7860/step \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<uuid>", "action_type": "execute", "sql": "ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT false"}'
```

**Run the baseline inference agent:**
```bash
export HF_TOKEN=your_token_here
python inference.py --host http://localhost:7860 --output results.json
```

**Environment variables for `inference.py`:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `HF_TOKEN` | ✅ Yes | — | Hugging Face / API key |
| `API_BASE_URL` | No | `https://api.openai.com/v1` | LLM endpoint |
| `MODEL_NAME` | No | `gpt-4.1-mini` | Model identifier |
| `MIGRATEENV_HOST` | No | `http://localhost:7860` | Environment server URL |

## Baseline Performance Scores

Scores below are from `results_final.json`, produced by the deterministic-first baseline agent (`inference.py`) against a live PostgreSQL instance.

| Task | Difficulty | Score | Schema Match | Data Integrity | FK Integrity | Efficiency | Expected (PRD) |
|---|---|---|---|---|---|---|---|
| Easy — Add column | Easy | **0.73** | 0.40 | 1.00 | 1.00 | 1.00 | ~0.82 |
| Medium — Table split | Medium | **0.49** | 0.40 | 0.25 | 0.80 | 1.00 | ~0.58 |
| Hard — Version upgrade | Hard | **0.75** | 0.70 | 0.75 | 1.00 | 1.00 | ~0.28 |

> The hard task outperforms PRD expectations significantly. Easy and medium scores are below PRD targets primarily due to partial schema_match — data integrity and efficiency components are strong.

## Submission Notes

- `inference.py` is in the project root ✅
- All LLM calls use the OpenAI client ✅
- `API_BASE_URL` and `MODEL_NAME` have default values ✅
- `HF_TOKEN` is required and raises `ValueError` if missing ✅
- `GET /openenv.yaml` serves the environment metadata ✅
- `GET /health` returns `{"status": "ok"}` for Space health checks ✅
