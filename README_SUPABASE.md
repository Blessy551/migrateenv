# MigrateEnv — Supabase Setup Guide

## What changed from the Postgres version

| File | Change |
|------|--------|
| `app/db/loader.py` | Removed `DROP DATABASE / CREATE DATABASE`. Reset now runs `northwind.sql` directly (it already has `DROP TABLE IF EXISTS` for every table). Task tables (`product_pricing`, `audit_log`) are dropped first via `CASCADE`. |
| `app/db/connection.py` | Uses `NullPool` + `sslmode=require` for Supabase compatibility. Normalises `postgres://` → `postgresql://`. |
| `docker-compose.yml` | Local Postgres service removed. App reads `DATABASE_URL` from `.env`. |
| `Dockerfile` | Removed SQLite default for `DATABASE_URL`. |

## Supabase free tier setup (5 minutes)

1. Go to **Supabase Dashboard → Project Settings → Database → Connection Pooling**
2. Set Mode to **Session** (port **5432**)  
   ⚠️ Do NOT use Transaction mode (port 6543) — it breaks `ALTER TABLE` statements mid-session.
3. Copy the connection string.

## Local run

```bash
cp .env.example .env
# Paste your Supabase Session pooler URL into DATABASE_URL in .env

docker compose up --build
# → App starts on http://localhost:7860
# → /health confirms DB connectivity
```

## Verify it works

```bash
curl -X POST http://localhost:7860/reset -H "Content-Type: application/json" \
     -d '{"task_id": "easy"}'
# Should return the Northwind customers schema observation

curl http://localhost:7860/health
# {"status":"ok","database_connected":true,...}
```

## Common issues

| Error | Fix |
|-------|-----|
| `SSL connection is required` | Make sure your URL starts with `postgresql://` and the loader uses `sslmode=require` (already done). |
| `prepared statement already exists` | Switch from Transaction pooler (6543) to Session pooler (5432). |
| `ERROR: permission denied for table` | Supabase free tier: connect as `postgres` user (the project owner), not `anon`. |
| `could not connect to server` | Check project is not paused (free tier pauses after 1 week of inactivity). |
