"""
EASY TASK — Add column + CHECK constraint to Northwind `customers` table.

Goal:
  1. ADD COLUMN loyalty_tier VARCHAR(20) DEFAULT 'standard' NOT NULL
  2. ADD CHECK CONSTRAINT chk_loyalty_tier ensuring tier is one of:
     ('standard', 'silver', 'gold', 'platinum')

Success criteria:
  - Column 'loyalty_tier' exists on 'customers'
  - CHECK constraint 'chk_loyalty_tier' is present
  - All 91 customer rows are preserved
  - No FK violations
"""
from app.tasks.base import BaseTask


class EasyTask(BaseTask):
    task_id = "easy"
    difficulty = "easy"
    description = (
        "Add a 'loyalty_tier' column (VARCHAR(20), DEFAULT 'standard', NOT NULL) "
        "to the Northwind 'customers' table, then add a CHECK constraint "
        "'chk_loyalty_tier' enforcing that the value is one of: "
        "standard, silver, gold, platinum."
    )
    target_description = (
        "customers table has loyalty_tier column with CHECK constraint chk_loyalty_tier. "
        "All 91 original rows are preserved."
    )
    max_steps = 10
    target_reward = 0.95

    def get_initial_observation_data(self):
        return {
            "focus_tables": ["customers"],
            "northwind_note": "The customers table has 91 rows with customer_id (VARCHAR 5) as PK.",
            "task_goal": self.description,
        }

    def get_hint(self) -> str:
        return (
            "Step 1: ALTER TABLE customers ADD COLUMN loyalty_tier VARCHAR(20) DEFAULT 'standard' NOT NULL; "
            "Step 2: ALTER TABLE customers ADD CONSTRAINT chk_loyalty_tier "
            "CHECK (loyalty_tier IN ('standard', 'silver', 'gold', 'platinum'));"
        )

    def get_target_schema_requirements(self):
        return {
            "table": "customers",
            "required_columns": [
                {
                    "name": "loyalty_tier",
                    "type_contains": "VARCHAR",
                    "nullable": False,
                }
            ],
            "required_check_constraints": [
                {
                    "name": "chk_loyalty_tier",
                    "sqltext_contains": "loyalty_tier",
                }
            ],
            "required_row_counts": {
                "customers": 91,
            },
        }
