# MigrateEnv - Executive Summary: Why You're Getting success=false

## The One-Sentence Problem

**Your implementation is a generic Northwind database migration environment, but the PRD defines three specific, deterministic tasks with clear requirements. You're grading a generic schema match instead of grading specific task completion.**

---

## What's Happening Now (Wrong)

```
[STEP] step=1 action=SELECT ... reward=0.73 done=false error=null
[END] success=false steps=1 rewards=0.73
```

### Why:

1. ❌ **LLM only inspects** → Gets reward for schema observation
2. ❌ **LLM stops** → Doesn't know what's needed to complete task
3. ❌ **success=false** → Threshold is 0.9, single step gives 0.73
4. ❌ **LLM never executes** → Because the observation doesn't tell it WHAT to execute

### Root Cause:

The task definition is vague. The LLM receives:
```json
{
  "task_id": "easy",
  "task_description": "Migrate Northwind database",  // ← Too generic!
  "current_schema": {
    "Customers": {...},  // ← Generic tables
    "Orders": {...},
    "Products": {...}
  }
}
```

The LLM thinks: "Schema already looks fine, maybe I'm done?" → stops after step 1.

---

## What Should Happen (Right)

```
[STEP] step=1 action=SELECT ... reward=0.2 done=false
[STEP] step=2 action=ALTER TABLE users ADD COLUMN is_verified... reward=0.65 done=false
[STEP] step=3 action=UPDATE users SET is_verified = true... reward=0.95 done=true
[END] success=true steps=3 rewards=0.2,0.45,0.30
```

### Why:

1. ✅ **Task is specific** → "Add is_verified column to users table"
2. ✅ **LLM knows exactly what's needed** → Target spec says what's missing
3. ✅ **LLM executes steps** → Because the observation clearly shows what's incomplete
4. ✅ **Rewards increase** → As task completion increases
5. ✅ **success=true** → Because reward reaches 0.9+ when task is fully complete

---

## The Three PRD Tasks (What You Should Be Doing)

### Task 1: Easy ⭐ (Add Column)
- **What**: Add `is_verified` BOOLEAN column to users table
- **Backfill**: Users created > 30 days ago → is_verified = true
- **Grading**:
  - Column schema correct: 0.4
  - All rows have value: 0.3
  - Old users correctly backfilled: 0.3
- **Expected**: Easy to complete in 3-4 steps, reward > 0.85

### Task 2: Medium (Table Split)
- **What**: Split orders table → orders + shipments
- **Grading** (0.25 each):
  - orders schema correct
  - shipments schema + FK correct
  - 200 rows preserved in orders
  - 200 rows moved to shipments with correct FK
- **Expected**: 5-8 steps, reward > 0.60

### Task 3: Hard (Full Migration)
- **What**: v1→v3 migration (rename columns, type change, new table, index)
- **Grading** (0.35+0.25+0.15+0.10+0.10):
  - Schema diff: 0.35
  - Data preserved: 0.25
  - Rename split correctly: 0.15
  - Type coercion valid: 0.10
  - Index created: 0.10
- **Expected**: 10-15 steps, reward > 0.30

---

## The Fix: 3-Step Implementation

### Step 1: Define PRD-Aligned Tasks (HIGH IMPACT)

Replace your generic task definitions with specific ones:

```python
# app/tasks/task_easy.py
class TaskAddColumn(BaseTask):
    task_id = "easy"
    description = "Add is_verified column to users and backfill based on created_at"
    
    def get_target_schema_requirements(self):
        return {
            "table": "users",
            "required_columns": [
                # ... existing columns ...
                {
                    "name": "is_verified",
                    "type": "BOOLEAN",
                    "nullable": False,
                    "default": "false",
                    "new_column": True,  # ← This column must be ADDED
                }
            ],
            "data_checks": [
                {
                    "check_type": "column_not_null",
                    "column": "is_verified"
                },
                {
                    "check_type": "conditional_value",
                    "condition": "created_at > NOW() - INTERVAL '30 days'",
                    "column": "is_verified",
                    "expected_value": True
                }
            ]
        }
```

**Files to create:**
- `app/tasks/task_easy.py` (Task 1)
- `app/tasks/task_medium.py` (Task 2)
- `app/tasks/task_hard.py` (Task 3)

### Step 2: Create Task-Specific Graders (HIGH IMPACT)

Replace generic graders with specific ones that check PRD requirements:

```python
# app/graders/task_easy_grader.py
class TaskEasyGrader:
    def grade(self, engine, requirements):
        # Check 1: is_verified column exists (BOOLEAN, NOT NULL, DEFAULT false)
        schema_score = self._check_schema(...)  # 0.4
        
        # Check 2: All rows have is_verified set (not NULL)
        data_score = self._check_data_integrity(...)  # 0.3
        
        # Check 3: Rows > 30 days old have is_verified = true
        backfill_score = self._check_backfill(...)  # 0.3
        
        return (schema_score * 0.4) + (data_score * 0.3) + (backfill_score * 0.3)
```

**Files to create:**
- `app/graders/task_easy_grader.py`
- `app/graders/task_medium_grader.py`
- `app/graders/task_hard_grader.py`

### Step 3: Update LLM System Prompt (MEDIUM IMPACT)

Tell the LLM exactly what each task requires:

```python
SYSTEM_PROMPT = """...
TASK 1 (Easy): Add is_verified column
- Add BOOLEAN column `is_verified` to users table
- Constraints: NOT NULL, DEFAULT false
- Backfill: Users created > 30 days ago must have is_verified = true
- Steps needed: 3-4
- Expected reward: > 0.85

TASK 2 (Medium): Split orders table
- Split orders → orders + shipments
- ... (detailed spec) ...

TASK 3 (Hard): v1→v3 migration
- Rename columns, change types, add tables, create indexes
- ... (detailed spec) ...
"""
```

---

## Why This Fixes It

### Current Problem Loop:
1. LLM gets vague task ("Migrate Northwind")
2. LLM inspects and sees data exists
3. LLM thinks schema is fine (it's generic Northwind, not specific to task)
4. LLM sends "done" or stops iterating
5. Grader scores generic schema match (0.73)
6. success=false because 0.73 < 0.90

### New Problem Loop:
1. LLM gets specific task ("Add is_verified to users, backfill > 30 days old")
2. LLM inspects and sees users table has NO is_verified column
3. LLM knows exactly what to do
4. LLM executes: ALTER TABLE users ADD COLUMN...
5. LLM executes: UPDATE users SET is_verified = true WHERE...
6. Grader scores task completion (0.95)
7. success=true because 0.95 ≥ 0.90

---

## Priority: What to Do First

| Priority | Action | File(s) | Impact |
|----------|--------|---------|--------|
| **HIGHEST** | Create Task 1 definition | `app/tasks/task_easy.py` | LLM will know what to execute |
| **HIGHEST** | Create Task 1 grader | `app/graders/task_easy_grader.py` | Will score task completion correctly |
| **HIGH** | Update LLM prompt | `inference.py` | Will guide LLM better |
| HIGH | Create Task 2 & 3 | Similar to above | Complete the suite |
| MEDIUM | Update Observation model | `app/models.py` | Better feedback |
| MEDIUM | Fix reward formula | `app/graders/composite.py` | Match PRD formula |

---

## Quick Test After Fixing

```bash
# After creating task_easy.py and task_easy_grader.py:
python inference.py --host https://blessy-karen-migrateenv.hf.space --debug --tasks easy

# Expected output:
[START] task=easy ...
[STEP] step=1 action=SELECT ... reward=0.2 done=false
[STEP] step=2 action=ALTER TABLE users ADD COLUMN ... reward=0.65 done=false
[STEP] step=3 action=UPDATE users SET ... reward=0.95 done=true
[END] success=true steps=3 rewards=0.2,0.45,0.30
```

If you see multiple [STEP] lines and success=true, the fix is working!

---

## Reference Code (Copy-Paste Ready)

I've provided:
1. **COMPLETE_PRD_ALIGNMENT_FIX.md** - Detailed explanation of all issues
2. **task_easy_PRD_aligned.py** - Ready-to-use Task 1 definition
3. **task_easy_grader_PRD_aligned.py** - Ready-to-use Task 1 grader

**How to use:**
1. Copy `task_easy_PRD_aligned.py` → `app/tasks/task_easy.py`
2. Copy `task_easy_grader_PRD_aligned.py` → `app/graders/task_easy_grader.py`
3. Update `app/env.py` or `app/main.py` to use TaskEasyGrader instead of generic grader
4. Test with: `python inference.py --host <url> --debug --tasks easy`

---

## The Key Insight

**You're building a generic database migration environment, but you submitted a PRD for a specific, deterministic task suite.**

The PRD says:
- Task 1: Add a specific column to a specific table, backfill based on specific logic
- Task 2: Split a specific table in a specific way
- Task 3: Perform a specific migration with specific schema changes

Your current implementation:
- Task 1: "Migrate Northwind database" (too vague)
- Task 2: (same, too vague)
- Task 3: (same, too vague)

**Align your implementation to the PRD, and success will follow.** The PRD is your source of truth for what tasks should be, what grading should check, and what the LLM should be told to do.

---

## Final Checklist

Before submission:
- [ ] Task 1 definition matches PRD (specific to is_verified column)
- [ ] Task 1 grader checks PRD requirements (schema + data + backfill)
- [ ] Task 2 definition matches PRD (specific to orders/shipments split)
- [ ] Task 2 grader checks PRD requirements
- [ ] Task 3 definition matches PRD (v1→v3 migration details)
- [ ] Task 3 grader checks PRD requirements
- [ ] LLM prompt updated with task-specific instructions
- [ ] Testing shows: multiple [STEP] lines, increasing rewards, success=true
- [ ] Expected baseline scores: Task 1: ~0.85, Task 2: ~0.60, Task 3: ~0.30

You've got this! The fix is straightforward: align the implementation to the PRD. The code is provided, the pattern is clear, and the impact will be immediate. 🚀
