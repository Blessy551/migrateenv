# MigrateEnv: Merge Working Code + HF Spaces Setup

## The Problem

Your **working code** has all the right logic but **doesn't have the HF Spaces connectivity**.
Your **new code** has HF Spaces connectivity but **is missing critical methods** (`get_initial_observation_data()`).

## The Solution: Copy Working Files + Keep Connectivity

Here's exactly which files to use:

---

## **Phase 1: Copy These Files From Working Code**

### ✅ **1. app/tasks/base.py**
**Source:** `/migrateenv_working/migrateenv/app/tasks/base.py`
**Action:** Replace current with working version (has `get_initial_observation_data()`)

### ✅ **2. app/tasks/task_easy.py**
**Source:** `/migrateenv_working/migrateenv/app/tasks/task_easy.py`
**Action:** Use as-is (it's the working implementation)

Key points in this file:
```python
class EasyTask(BaseTask):
    def get_initial_observation_data(self):
        return {
            "focus_tables": ["customers"],
            "northwind_note": "...",
            "task_goal": self.description,
        }
    
    def get_hint(self) -> str:
        return "Step 1: ALTER TABLE..."
    
    def get_target_schema_requirements(self):
        return {
            "table": "customers",
            "required_columns": [...],
            "required_check_constraints": [...],
            "required_row_counts": {...},
        }
```

### ✅ **3. app/tasks/task_medium.py**
**Source:** `/migrateenv_working/migrateenv/app/tasks/task_medium.py`
**Action:** Use as-is

### ✅ **4. app/tasks/task_hard.py**
**Source:** `/migrateenv_working/migrateenv/app/tasks/task_hard.py`
**Action:** Use as-is

### ✅ **5. app/graders/composite.py**
**Source:** `/migrateenv_working/migrateenv/app/graders/composite.py`
**Action:** Use as-is (working grader logic)

### ✅ **6. app/graders/schema_grader.py**
**Source:** `/migrateenv_working/migrateenv/app/graders/schema_grader.py`
**Action:** Use as-is

### ✅ **7. app/graders/data_grader.py**
**Source:** `/migrateenv_working/migrateenv/app/graders/data_grader.py`
**Action:** Use as-is

### ✅ **8. app/graders/fk_grader.py**
**Source:** `/migrateenv_working/migrateenv/app/graders/fk_grader.py`
**Action:** Use as-is

### ✅ **9. app/graders/base.py**
**Source:** `/migrateenv_working/migrateenv/app/graders/base.py`
**Action:** Use as-is

---

## **Phase 2: Keep These Files From Current Code**

These have the HF Spaces / PostgreSQL connectivity:

### 🔗 **Keep (don't replace):**
- ✅ `app/main.py` - Has HF Spaces endpoints
- ✅ `app/env.py` - Has PostgreSQL + Supabase logic
- ✅ `app/models.py` - Has updated Observation model
- ✅ `app/db/` - All database connection files
- ✅ `app/sanitizer.py` - SQL sanitizer
- ✅ `app/inspector.py` - Schema inspector
- ✅ `inference.py` - Your inference script
- ✅ `Dockerfile` - HF Spaces Docker setup
- ✅ `docker-compose.yml` - All services

---

## **Phase 3: One Critical Fix**

Your current `app/tasks/__init__.py` might have the wrong class names. Update it:

```python
# app/tasks/__init__.py
from app.tasks.task_easy import EasyTask      # ← Not "TaskAddColumn"
from app.tasks.task_medium import MediumTask  # ← Not "TaskTableSplit"
from app.tasks.task_hard import HardTask      # ← Not "TaskVersionUpgrade"

TASK_REGISTRY = {
    "easy": EasyTask,
    "medium": MediumTask,
    "hard": HardTask,
}
```

---

## **Phase 4: Quick Copy-Paste Instructions**

```bash
# 1. Copy base task class
cp /mnt/user-data/uploads/migrateenv/migrateenv/app/tasks/base.py \
   D:\SCALER\ HACKATHON\migrateenv\app\tasks\base.py

# 2. Copy task implementations
cp /mnt/user-data/uploads/migrateenv/migrateenv/app/tasks/task_*.py \
   D:\SCALER\ HACKATHON\migrateenv\app\tasks\

# 3. Copy grader implementations
cp /mnt/user-data/uploads/migrateenv/migrateenv/app/graders/composite.py \
   D:\SCALER\ HACKATHON\migrateenv\app\graders\composite.py

cp /mnt/user-data/uploads/migrateenv/migrateenv/app/graders/schema_grader.py \
   D:\SCALER\ HACKATHON\migrateenv\app\graders\schema_grader.py

cp /mnt/user-data/uploads/migrateenv/migrateenv/app/graders/data_grader.py \
   D:\SCALER\ HACKATHON\migrateenv\app\graders\data_grader.py

cp /mnt/user-data/uploads/migrateenv/migrateenv/app/graders/fk_grader.py \
   D:\SCALER\ HACKATHON\migrateenv\app\graders\fk_grader.py

cp /mnt/user-data/uploads/migrateenv/migrateenv/app/graders/base.py \
   D:\SCALER\ HACKATHON\migrateenv\app\graders\base.py
```

---

## **What You'll Get**

**Before (Broken):**
```
[END] success=false steps=0 rewards=0.00
Error: Can't instantiate abstract class EasyTask with abstract method get_initial_observation_data
```

**After (Working):**
```
[STEP] step=1 action=... reward=0.2 done=false
[STEP] step=2 action=... reward=0.6 done=false
[STEP] step=3 action=... reward=0.95 done=true
[END] success=true steps=3 rewards=0.2,0.4,0.35
```

---

## **Test After Merge**

```powershell
# Make sure server is running
uvicorn app.main:app --host 0.0.0.0 --port 8000

# In another PowerShell:
python inference.py --host http://localhost:8000 --debug --tasks easy
```

Expected: Multiple [STEP] lines, success=true ✅

---

## **Key Files Comparison**

| File | Source | Reason |
|------|--------|--------|
| tasks/base.py | Working | Has `get_initial_observation_data()` |
| tasks/task_*.py | Working | Fully implemented with all methods |
| graders/composite.py | Working | Correct grading logic |
| graders/schema_grader.py | Working | Correct schema evaluation |
| graders/data_grader.py | Working | Correct data integrity checks |
| graders/fk_grader.py | Working | Correct FK validation |
| app/main.py | Current | Has HF Spaces endpoints |
| app/env.py | Current | Has PostgreSQL/Supabase setup |
| inference.py | Current | Has HF Spaces connectivity |
| Dockerfile | Current | HF Spaces compatible |

---

## **Why This Works**

1. ✅ **Working code**: All methods implemented, grading logic correct
2. ✅ **Current code**: HF Spaces connectivity, PostgreSQL support
3. ✅ **Combined**: Best of both worlds
4. ❌ **Previous attempt**: Tried to merge by hand, missed `get_initial_observation_data()`

---

## **One More Thing: tasks/__init__.py**

Make sure your `__init__.py` is correct:

```python
# app/tasks/__init__.py
from app.tasks.task_easy import EasyTask
from app.tasks.task_medium import MediumTask
from app.tasks.task_hard import HardTask

TASK_REGISTRY = {
    "easy": EasyTask,
    "medium": MediumTask,
    "hard": HardTask,
}
```

The class names are:
- `EasyTask` (not TaskAddColumn)
- `MediumTask` (not TaskTableSplit)  
- `HardTask` (not TaskVersionUpgrade)

---

## **Ready to Merge?**

1. Copy the 9 files listed above from working code
2. Fix `tasks/__init__.py` to use correct class names
3. Test with: `python inference.py --host http://localhost:8000 --debug --tasks easy`
4. Should show multiple steps and success=true ✅

This merges the proven logic with HF Spaces connectivity. No more abstract method errors!
