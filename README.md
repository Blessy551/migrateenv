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

MigrateEnv is a real-world SQL migration environment for evaluating agent behavior on schema evolution tasks. An agent must inspect a live database, apply migrations safely, preserve row integrity, and explicitly finish the episode with a `done` action.

## Motivation

Schema migration is one of the most failure-prone tasks in software engineering. This environment focuses on high-signal migration workflows instead of toy interactions:

- add and backfill a new column safely
- split a mixed table into normalized tables
- apply a multi-table version upgrade without losing data

## Action Space

The environment accepts structured actions through `POST /step`:

- `inspect`: ask the server for current schema and row-count context
- `execute`: run one SQL statement
- `rollback`: record a rollback attempt and return graded feedback
- `done`: trigger final grading and end the episode

Request body:

```json
{
  "session_id": "uuid",
  "action_type": "execute",
  "sql": "ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT false"
}
```

## Observation Space

Each observation includes:

- `task_id`
- `task_description`
- `difficulty`
- `hint`
- `target_spec`
- `current_schema`
- `row_counts`
- `step_number`
- `max_steps`
- `target_description`
- `last_action_result`
- `focus_tables`

## Tasks

### Easy

Add `is_verified` to `users`, backfill older accounts, and preserve all 50 rows.

### Medium

Split a mixed `orders` table into `orders` and `shipments` while preserving all 200 rows and reconnecting the foreign key.

### Hard

Upgrade a small commerce schema by splitting `users.fullname`, coercing numeric product prices, adding `discounts`, and creating an index for unfinished orders while preserving seeded data.

## Setup

1. Copy `.env.example` to `.env`
2. Set `DATABASE_URL`
3. Build and run the app

```bash
docker build -t migrateenv .
docker run --env-file .env -p 7860:7860 migrateenv
```

For local development:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 7860
```

## Usage

Reset an episode:

```bash
curl -X POST http://localhost:7860/reset \
  -H "Content-Type: application/json" \
  -d "{\"task_id\":\"easy\"}"
```

Run the baseline agent:

```bash
python inference.py --host http://localhost:7860 --output results_local.json
```

## Baseline Scores

The deterministic-first baseline is designed to complete all three tasks reliably once the environment is running against a valid PostgreSQL instance. Capture and update the latest score artifact before submission.

## Submission Notes

- `inference.py` is in the project root
- OpenAI client is used for model fallback
- `API_BASE_URL` and `MODEL_NAME` have defaults
- `HF_TOKEN` is required
- `GET /openenv.yaml` serves the environment metadata
