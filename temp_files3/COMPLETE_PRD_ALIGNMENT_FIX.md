# MigrateEnv - Complete PRD Alignment Fix Guide

## The Core Problem

Your implementation **deviates significantly from the PRD**. The PRD defines three specific, deterministic tasks with clear grading criteria. Your current implementation uses a generic Northwind database with mismatched graders.

---

## Issue #1: Tasks Don't Match PRD ❌

### PRD Definition (What Should Be)

**Task 1 (Easy) - task_add_column:**
- Table: `users` (id, email, created_at)
- Add: `is_verified` BOOLEAN DEFAULT false NOT NULL
- Backfill: users created > 30 days ago → `is_verified = true`
- Grading:
  - Column exists with correct type/default: 0.4
  - All 50 rows have is_verified set: 0.3
  - Rows > 30 days have is_verified=true: 0.3

**Task 2 (Medium) - task_table_split:**
- Split `orders` table into `orders` + `shipments`
- orders: (id, user_id, total, status, created_at)
- shipments: (id, order_id, address, city, postal_code, shipped_at) + FK to orders
- Grading (0.25 each):
  - orders schema matches
  - shipments schema + FK correct
  - 200 rows in orders, zero loss
  - 200 rows in shipments with correct order_id

**Task 3 (Hard) - task_version_upgrade:**
- Full v1→v3 migration (5 tables: users, products, orders, order_items, reviews)
- Changes:
  - Rename: `users.fullname` → split to `users.first_name` + `users.last_name`
  - Type change: `products.price` TEXT → NUMERIC(10,2)
  - New table: `discounts` with FK to orders
  - Index: partial index on orders(status) WHERE status != 'completed'
- Grading (0.35+0.25+0.15+0.10+0.10):
  - Schema diff score: 0.35
  - Data integrity (row counts): 0.25
  - first_name/last_name split correctly: 0.15
  - price coercion valid: 0.10
  - Partial index present: 0.10
  - Bonus: rollback without loss: +0.05

### Current Implementation (What You Have) ❌

- Using generic Northwind database
- Tasks not clearly defined per PRD spec
- Grader checks generic Northwind schema matching, not PRD requirements
- LLM gets confused about what's needed

---

## Issue #2: Reward Calculation Misaligned ❌

### PRD Reward Formula:
```
R_total = (
  0.45 × schema_match_score       # Primary objective
  + 0.25 × data_integrity_score   # Row preservation
  - 0.15 × step_penalty           # (steps_used / max_steps)^0.5
  - 0.10 × time_penalty           # (elapsed / time_limit) if > 80%
  + 0.05 × rollback_bonus         # BEGIN/ROLLBACK used correctly
)
```

### Current Implementation ❌

Uses a composite grader with:
- schema_score: 0.35
- data_score: 0.30
- fk_score: 0.20
- migration_bonus: 0.05
- efficiency_score: 0.05
- time_score: 0.05

**This doesn't match the PRD formula!**

---

## Issue #3: Task Definitions Missing ❌

The tasks in `app/tasks/` don't match PRD specifications. They should define:

```python
class TaskAddColumn(BaseTask):
    task_id = "easy"
    difficulty = "easy"
    description = "Add is_verified column to users table and backfill based on created_at"
    max_steps = 10
    time_limit = 60
    target_reward = 0.85
    
    def get_target_schema_requirements(self):
        return {
            "required_tables": ["users"],
            "required_columns": [
                {"table": "users", "name": "id"},
                {"table": "users", "name": "email"},
                {"table": "users", "name": "created_at"},
                {"table": "users", "name": "is_verified", 
                 "type": "BOOLEAN", "default": "false", "nullable": False},
            ],
            "required_data_checks": [
                {
                    "type": "column_not_null",
                    "table": "users",
                    "column": "is_verified",
                    "expected_count": 50
                },
                {
                    "type": "conditional_check",
                    "table": "users",
                    "condition": "created_at > now() - interval '30 days'",
                    "expected_column": "is_verified",
                    "expected_value": True
                }
            ]
        }
```

---

## Issue #4: Grader Checks Don't Match PRD ❌

The graders should check PRD-specific requirements, not generic Northwind schema matching.

For Task 1, grader should verify:
1. ✓ Column `is_verified` exists on users table
2. ✓ Type is BOOLEAN
3. ✓ Default is false
4. ✓ NOT NULL constraint
5. ✓ All 50 rows have is_verified value (not NULL)
6. ✓ Rows with created_at > 30 days ago have is_verified = true

For Task 2, grader should verify:
1. ✓ orders table has only: id, user_id, total, status, created_at
2. ✓ shipments table exists with: id, order_id, address, city, postal_code, shipped_at
3. ✓ FK constraint from shipments.order_id → orders.id
4. ✓ All 200 rows migrated correctly
5. ✓ No data loss

For Task 3, grader should verify:
1. ✓ users.fullname split → first_name + last_name
2. ✓ products.price is NUMERIC(10,2)
3. ✓ discounts table exists with FK to orders
4. ✓ Partial index on orders(status)
5. ✓ All row counts preserved
6. ✓ Price values correctly coerced (no NULLs, no truncation)

---

## Issue #5: LLM Gets Wrong Observation ❌

The LLM receives observation with generic Northwind schema, not PRD task specifics.

Should receive:
```json
{
  "task_id": "easy",
  "task_description": "Add is_verified column to users table...",
  "target_spec": {
    "users": {
      "columns": ["id", "email", "created_at", "is_verified"],
      "is_verified": {"type": "BOOLEAN", "default": false, "not_null": true}
    }
  },
  "current_schema": {...},
  "hint": "Step 1: ALTER TABLE users ADD COLUMN is_verified...\nStep 2: UPDATE users SET is_verified..."
}
```

Instead it's getting generic Northwind schema info.

---

## The Fix: Implementation Roadmap

### Step 1: Create PRD-Compliant Task Definitions

**File:** `app/tasks/task_easy.py`

```python
from app.tasks.base import BaseTask

class TaskAddColumn(BaseTask):
    task_id = "easy"
    difficulty = "easy"
    description = "Add is_verified boolean column to users table and backfill based on created_at"
    max_steps = 10
    time_limit = 60
    target_reward = 0.85
    target_description = "Users table must have is_verified column (BOOLEAN, NOT NULL, DEFAULT false). Users created > 30 days ago must have is_verified=true."
    
    def get_target_schema_requirements(self):
        """Return what the schema MUST look like at episode end."""
        return {
            "table": "users",
            "required_columns": [
                {"name": "id", "type": "INTEGER", "nullable": False},
                {"name": "email", "type": "VARCHAR", "nullable": False},
                {"name": "created_at", "type": "TIMESTAMP", "nullable": False},
                {"name": "is_verified", "type": "BOOLEAN", "nullable": False, "default": "false"},
            ],
            "required_data_checks": [
                {
                    "check_type": "column_not_null",
                    "table": "users",
                    "column": "is_verified",
                },
                {
                    "check_type": "conditional_values",
                    "table": "users",
                    "condition": "created_at > NOW() - INTERVAL '30 days'",
                    "column": "is_verified",
                    "expected_value": True,
                }
            ]
        }
    
    def get_hint(self):
        return """Step 1: Add the is_verified column to users table
ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT false;

Step 2: Update users created > 30 days ago
UPDATE users SET is_verified = true 
WHERE created_at > NOW() - INTERVAL '30 days';"""
    
    def reset_task(self, engine):
        """Initialize task with seed data (50 users, some created > 30 days ago)."""
        # ... seed users table ...
        pass
```

### Step 2: Rewrite Graders to Match PRD

**File:** `app/graders/task_grader.py`

```python
class TaskGrader:
    """Task-specific graders that match PRD definitions."""
    
    def grade_easy_add_column(self, engine, requirements):
        """Grade Task 1: is_verified column addition."""
        schema_score = self._check_schema(engine, requirements)  # 0.4
        data_score = self._check_data(engine, requirements)      # 0.3
        backfill_score = self._check_backfill(engine, requirements)  # 0.3
        
        total = schema_score * 0.4 + data_score * 0.3 + backfill_score * 0.3
        return {
            "schema_score": schema_score,
            "data_score": data_score,
            "backfill_score": backfill_score,
            "composite": total
        }
    
    def _check_schema(self, engine, requirements):
        """Check column exists with correct type/default/constraints."""
        # Query: SELECT column_name, data_type, column_default, is_nullable
        # FROM information_schema.columns
        # WHERE table_name='users' AND column_name='is_verified'
        # Verify: data_type='boolean', column_default='false', is_nullable='NO'
        pass
    
    def _check_data(self, engine, requirements):
        """Check all 50 users have is_verified set (not NULL)."""
        # Query: SELECT COUNT(*) FROM users WHERE is_verified IS NULL
        # Expect: 0
        # Score: COUNT(*) / 50
        pass
    
    def _check_backfill(self, engine, requirements):
        """Check users created > 30 days ago have is_verified=true."""
        # Query: SELECT COUNT(*) FROM users 
        # WHERE created_at > NOW() - INTERVAL '30 days' AND is_verified=false
        # Expect: 0
        # Score: 1 - (count / total_old_users)
        pass
```

### Step 3: Update Reward Calculation

**File:** `app/graders/reward.py`

```python
def compute_reward(engine, task_id, requirements, step_number, max_steps, elapsed, penalties):
    """Compute reward according to PRD formula."""
    
    # Get task-specific grader
    if task_id == "easy":
        grader_result = grade_easy_add_column(engine, requirements)
        schema_score = grader_result["schema_score"]        # 0-1
        data_score = grader_result["data_score"]            # 0-1
        backfill_score = grader_result["backfill_score"]    # 0-1
        
        # PRD: uses schema_match + data_integrity, not separate components
        schema_match = (schema_score * 0.4 + data_score * 0.3 + backfill_score * 0.3)  # Combined
        data_integrity = data_score  # Row preservation
    
    # Penalties
    step_penalty = 0.15 * ((step_number / max_steps) ** 0.5)
    time_penalty = 0.0
    if elapsed > (300 * 0.8):  # 80% of time limit
        time_penalty = 0.10 * (elapsed / 300.0)
    
    rollback_bonus = 0.05 if "used_rollback" in penalties else 0.0
    
    # PRD formula
    total_reward = (
        0.45 * schema_match 
        + 0.25 * data_integrity
        - step_penalty
        - time_penalty
        + rollback_bonus
    )
    
    return max(0.0, min(1.0, total_reward))
```

### Step 4: Update System Prompt for LLM

**File:** `inference.py` lines 97-162

```python
SYSTEM_PROMPT = """You are a database migration engineer solving specific migration tasks.

TASK 1 (Easy): Add is_verified column
- Task: Add BOOLEAN column `is_verified` to `users` table
- Default: false, NOT NULL
- Backfill: Users created > 30 days ago → is_verified = true
- Constraints: 10 steps max, 60 second limit
- Expected score: 0.85+

TASK 2 (Medium): Split orders table
- Task: Split monolithic `orders` table into `orders` + `shipments`
- orders: (id, user_id, total, status, created_at)
- shipments: (id, order_id, address, city, postal_code, shipped_at) with FK
- Constraints: 20 steps max, 120 second limit
- Expected score: 0.58+

TASK 3 (Hard): Migrate v1→v3 schema
- Task: Full schema migration with renames, type changes, new tables, indexes
- Changes:
  1. Split users.fullname → first_name + last_name
  2. Change products.price TEXT → NUMERIC(10,2)
  3. Add discounts table with FK to orders
  4. Add partial index: orders(status) WHERE status != 'completed'
  5. Preserve all rows from 5 tables (users, products, orders, order_items, reviews)
- Constraints: 40 steps max, 300 second limit
- Expected score: 0.30+

GRADING BREAKDOWN:
- Schema matching: 45% weight
- Data integrity: 25% weight
- Penalties:
  - Step efficiency: -15% (based on steps_used / max_steps)
  - Time penalty: -10% (if > 80% of time limit)
  - Rollback bonus: +5% (if used BEGIN/ROLLBACK cleanly)

WORKFLOW:
1. Read task_description and target_spec carefully
2. Inspect current schema
3. Plan migration steps based on requirements
4. Execute one step at a time
5. Verify results before marking done
6. Send "done" ONLY when task spec is fully met

RESPONSE FORMAT:
{"action_type": "inspect" | "execute" | "done", "sql": "...", "inspect_query": "..."}
"""
```

### Step 5: Update Observation Model

```python
class Observation(BaseModel):
    task_id: str
    task_description: str  # PRD task description
    difficulty: str
    target_spec: dict      # What schema MUST look like
    current_schema: dict   # Current state
    row_counts: dict
    step_number: int
    max_steps: int
    target_description: str
    hint: str              # Step-by-step hints per PRD
    grader_feedback: str = ""
    focus_tables: list[str] = []
```

---

## Priority Implementation Order

1. **HIGHEST:** Create PRD-aligned task definitions (app/tasks/)
2. **HIGH:** Rewrite task-specific graders (app/graders/)
3. **HIGH:** Fix reward formula (composite grader)
4. **MEDIUM:** Update system prompt in inference.py
5. **MEDIUM:** Update Observation model
6. **LOW:** Polish logging/debugging

---

## Expected Success Criteria

After implementing these fixes:

**Task 1 (Easy):**
- ✅ Multiple [STEP] lines (not just 1)
- ✅ Rewards increase: 0.4 → 0.7 → 0.8+
- ✅ success=true when reward ≥ 0.85
- ✅ Expected: 3-5 steps

**Task 2 (Medium):**
- ✅ Multiple [STEP] lines
- ✅ Rewards: 0.25 → 0.5 → 0.6+
- ✅ success=true when reward ≥ 0.85 (or PRD target)
- ✅ Expected: 5-10 steps

**Task 3 (Hard):**
- ✅ Multiple [STEP] lines
- ✅ Rewards: 0.1 → 0.2 → 0.3+
- ✅ success=true possible if hitting targets
- ✅ Expected: 8-20 steps

---

## Files to Create/Modify

| File | Action | Priority |
|------|--------|----------|
| app/tasks/task_easy.py | Create PRD Task 1 | HIGHEST |
| app/tasks/task_medium.py | Create PRD Task 2 | HIGHEST |
| app/tasks/task_hard.py | Create PRD Task 3 | HIGHEST |
| app/graders/task_grader.py | Create PRD graders | HIGH |
| app/graders/composite.py | Rewrite reward formula | HIGH |
| inference.py | Update system prompt | MEDIUM |
| app/models.py | Update Observation | MEDIUM |

---

## Testing Each Fix

```bash
# After each major change, test:
python inference.py --host <url> --debug --tasks easy
# Should see: multiple [STEP] lines, increasing rewards, eventually success=true
```

This is the path to passing your hackathon! The current implementation is too generic. The PRD defines specific, deterministic tasks with clear grading. Align to that, and success will follow.
