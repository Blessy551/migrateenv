# Quick Start: Merge Working Code in 5 Minutes

## **The Issue**
Your working code works perfectly locally, but when you tried to merge PRD changes, you lost the `get_initial_observation_data()` method that BaseTask requires.

## **The Solution**
Replace your broken task/grader files with the working ones. They already have everything you need.

---

## **Step 1: Backup (2 minutes)**

```bash
cd D:\SCALER HACKATHON\migrateenv

# Backup your current files
xcopy app\tasks app\tasks_backup /E /I /Y
xcopy app\graders app\graders_backup /E /I /Y
```

---

## **Step 2: Replace Tasks (1 minute)**

Copy these **WORKING** files from the output folder into your `app/tasks/`:

1. **base.py** → `app/tasks/base.py` ✅
2. **task_easy_WORKING.py** → Rename to `task_easy.py` in `app/tasks/` ✅
3. **task_medium_WORKING.py** → Rename to `task_medium.py` in `app/tasks/` ✅
4. **task_hard_WORKING.py** → Rename to `task_hard.py` in `app/tasks/` ✅

**Windows PowerShell:**
```powershell
# Download the files from outputs and place them:
Copy-Item "base.py" -Destination "D:\SCALER HACKATHON\migrateenv\app\tasks\base.py"
Copy-Item "task_easy_WORKING.py" -Destination "D:\SCALER HACKATHON\migrateenv\app\tasks\task_easy.py"
Copy-Item "task_medium_WORKING.py" -Destination "D:\SCALER HACKATHON\migrateenv\app\tasks\task_medium.py"
Copy-Item "task_hard_WORKING.py" -Destination "D:\SCALER HACKATHON\migrateenv\app\tasks\task_hard.py"
```

---

## **Step 3: Replace Graders (1 minute)**

Copy these **WORKING** files into your `app/graders/`:

1. **graders_base_WORKING.py** → Rename to `base.py` in `app/graders/` ✅
2. **composite_WORKING.py** → `app/graders/composite.py` ✅
3. **schema_grader_WORKING.py** → `app/graders/schema_grader.py` ✅
4. **data_grader_WORKING.py** → `app/graders/data_grader.py` ✅
5. **fk_grader_WORKING.py** → `app/graders/fk_grader.py` ✅

**Windows PowerShell:**
```powershell
Copy-Item "graders_base_WORKING.py" -Destination "D:\SCALER HACKATHON\migrateenv\app\graders\base.py"
Copy-Item "composite_WORKING.py" -Destination "D:\SCALER HACKATHON\migrateenv\app\graders\composite.py"
Copy-Item "schema_grader_WORKING.py" -Destination "D:\SCALER HACKATHON\migrateenv\app\graders\schema_grader.py"
Copy-Item "data_grader_WORKING.py" -Destination "D:\SCALER HACKATHON\migrateenv\app\graders\data_grader.py"
Copy-Item "fk_grader_WORKING.py" -Destination "D:\SCALER HACKATHON\migrateenv\app\graders\fk_grader.py"
```

---

## **Step 4: Fix tasks/__init__.py (1 minute)**

Edit `app/tasks/__init__.py` and make sure it has:

```python
from app.tasks.task_easy import EasyTask
from app.tasks.task_medium import MediumTask
from app.tasks.task_hard import HardTask

TASK_REGISTRY = {
    "easy": EasyTask,
    "medium": MediumTask,
    "hard": HardTask,
}
```

**Key:** Class names are:
- `EasyTask` (not `TaskAddColumn`)
- `MediumTask` (not `TaskTableSplit`)
- `HardTask` (not `TaskVersionUpgrade`)

---

## **Step 5: Test (30 seconds)**

```powershell
cd D:\SCALER HACKATHON\migrateenv

# Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000

# In another PowerShell:
python inference.py --host http://localhost:8000 --debug --tasks easy
```

**Expected Output:**
```
[START] task=easy env=migrateenv model=llama-3.3-70b-versatile
[STEP] step=1 action=... reward=0.2 done=false
[STEP] step=2 action=... reward=0.6 done=false
[STEP] step=3 action=... reward=0.95 done=true
[END] success=true steps=3 rewards=0.2,0.4,0.35
```

---

## **What Changed?**

| Component | Before | After |
|-----------|--------|-------|
| BaseTask | Missing `get_initial_observation_data()` | ✅ Has it |
| EasyTask | Abstract method error | ✅ Fully implemented |
| MediumTask | Abstract method error | ✅ Fully implemented |
| HardTask | Abstract method error | ✅ Fully implemented |
| Graders | Generic graders | ✅ Proven working logic |
| HF Spaces | ✅ Still there | ✅ Still there |

---

## **If It Works (Next Steps)**

Once you see `success=true` output:

1. **Commit and push to HF:**
   ```bash
   git add -A
   git commit -m "Merge working code with HF Spaces connectivity"
   git push origin main
   ```

2. **Test on HF Spaces:**
   ```powershell
   python inference.py --host https://blessy-karen-migrateenv.hf.space --debug --tasks easy
   ```

3. **Run full evaluation:**
   ```powershell
   python inference.py --host https://blessy-karen-migrateenv.hf.space --output results_final.json
   ```

---

## **If It Still Fails**

Check:

1. **Is tasks/__init__.py correct?**
   ```bash
   # View it
   cat app\tasks\__init__.py
   ```

2. **Are the files in the right place?**
   ```bash
   dir app\tasks\
   # Should show: base.py, task_easy.py, task_medium.py, task_hard.py
   
   dir app\graders\
   # Should show: base.py, composite.py, schema_grader.py, data_grader.py, fk_grader.py
   ```

3. **Run the diagnostic:**
   ```bash
   curl http://localhost:8000/tasks
   # Should return task list without errors
   ```

---

## **Summary**

You have:
- ✅ **Working task definitions** (from your old code)
- ✅ **Working graders** (from your old code)
- ✅ **HF Spaces connectivity** (from new code)

Just swap the files and it should work!

**Time to fix: 5 minutes**
**Expected result: success=true** ✅
