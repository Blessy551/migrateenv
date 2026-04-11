"""
Task 1 (Easy): Add is_verified Column to Users Table

PRD Specification:
- Add boolean column `is_verified` to users table with DEFAULT false and NOT NULL
- Backfill: Users created > 30 days ago must have is_verified = true
- Starting: 50 seed rows with varying created_at
- Grading:
  * Column exists with correct type/default: 0.4
  * All 50 rows have is_verified set (not NULL): 0.3
  * Rows > 30 days old have is_verified=true: 0.3
- Max steps: 10 | Time limit: 60s | Expected score: ~0.85
"""
from __future__ import annotations
from app.tasks.base import BaseTask

class TaskAddColumn(BaseTask):
    task_id = "easy"
    difficulty = "easy"
    description = "Add is_verified boolean column to users table and backfill based on created_at age"
    max_steps = 10
    time_limit = 60
    target_reward = 0.85
    
    def get_target_description(self) -> str:
        return """Target Schema for Task 1:
        
users table MUST have:
- Column: id (INTEGER, PRIMARY KEY)
- Column: email (VARCHAR, NOT NULL)
- Column: created_at (TIMESTAMP, NOT NULL)
- Column: is_verified (BOOLEAN, NOT NULL, DEFAULT false)

Data Requirements:
- All 50 users must have is_verified set (no NULLs)
- Users with created_at > 30 days ago: is_verified = true
- Users with created_at ≤ 30 days ago: is_verified = false (default)"""

    def get_hint(self) -> str:
        return """Step-by-step migration plan:

Step 1: Inspect current users table schema
SELECT column_name, data_type, is_nullable, column_default 
FROM information_schema.columns 
WHERE table_name='users';

Step 2: Add the is_verified column
ALTER TABLE users 
ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT false;

Step 3: Backfill users created > 30 days ago
UPDATE users 
SET is_verified = true 
WHERE created_at > NOW() - INTERVAL '30 days';

Step 4: Verify the migration
SELECT COUNT(*) as total, 
       COUNT(is_verified) as non_null_count,
       COUNT(CASE WHEN is_verified = true THEN 1 END) as true_count
FROM users;"""

    def get_target_schema_requirements(self) -> dict:
        """Return PRD-compliant schema requirements."""
        return {
            "table": "users",
            "required_tables": ["users"],
            "required_columns": [
                {
                    "name": "id",
                    "type": "INTEGER",
                    "nullable": False,
                },
                {
                    "name": "email",
                    "type": "VARCHAR",
                    "nullable": False,
                },
                {
                    "name": "created_at",
                    "type": "TIMESTAMP",
                    "nullable": False,
                },
                {
                    "name": "is_verified",
                    "type": "BOOLEAN",
                    "nullable": False,
                    "default": "false",
                    "new_column": True,  # Flag: this column must be added
                },
            ],
            "data_checks": [
                {
                    "check_type": "column_not_null",
                    "table": "users",
                    "column": "is_verified",
                    "description": "All 50 rows must have is_verified set (not NULL)",
                },
                {
                    "check_type": "conditional_value",
                    "table": "users",
                    "condition": "created_at > NOW() - INTERVAL '30 days'",
                    "column": "is_verified",
                    "expected_value": True,
                    "description": "Users created > 30 days ago must have is_verified=true",
                },
                {
                    "check_type": "row_count",
                    "table": "users",
                    "expected_count": 50,
                    "description": "All 50 seed rows must be preserved",
                },
            ],
        }

    def reset_task(self, engine):
        """Initialize task with clean schema and seed data."""
        from sqlalchemy import text
        
        with engine.begin() as conn:
            # Drop table if exists
            conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            
            # Create users table WITHOUT is_verified column
            conn.execute(text("""
                CREATE TABLE users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
            """))
            
            # Seed 50 users with varying created_at (mix of old and new)
            # 25 users created > 30 days ago, 25 created recently
            conn.execute(text("""
                INSERT INTO users (email, created_at) VALUES
            """) + ",\n".join([
                f"('user{i}@example.com', NOW() - INTERVAL '{30 + i*2} days')"
                if i < 25
                else f"('user{i}@example.com', NOW() - INTERVAL '{5 + (i-25)*2} days')"
                for i in range(50)
            ]))
