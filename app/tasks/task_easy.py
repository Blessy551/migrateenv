"""
EASY TASK - Add and backfill is_verified on users.
"""
from app.tasks.base import BaseTask


class EasyTask(BaseTask):
    task_id = "easy"
    difficulty = "easy"
    description = (
        "Add an is_verified boolean column to users with a default of false, "
        "make it non-null, and backfill users older than 30 days to true."
    )
    target_description = (
        "users includes is_verified BOOLEAN DEFAULT false NOT NULL and all 50 rows are preserved "
        "with older accounts correctly backfilled to true."
    )
    max_steps = 10
    target_reward = 0.95

    def get_initial_observation_data(self):
        return {
            "focus_tables": ["users"],
            "seed_note": "users starts with 50 rows and columns id, email, created_at.",
            "task_goal": self.description,
        }

    def get_hint(self) -> str:
        return (
            "Step 1: ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT false; "
            "Step 2: UPDATE users SET is_verified = true WHERE created_at < NOW() - INTERVAL '30 days'; "
            "Step 3: ALTER TABLE users ALTER COLUMN is_verified SET NOT NULL;"
        )

    def get_target_schema_requirements(self):
        return {
            "task_grader": "easy",
            "table": "users",
            "required_columns": [{"name": "is_verified", "type_contains": "BOOLEAN", "nullable": False}],
            "required_row_counts": {"users": 50},
        }
