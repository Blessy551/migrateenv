"""
MEDIUM TASK - Split orders into orders plus shipments.
"""
from app.tasks.base import BaseTask


class MediumTask(BaseTask):
    task_id = "medium"
    difficulty = "medium"
    description = (
        "Split a monolithic orders table into a normalized orders table and a shipments table, "
        "preserving all 200 rows and linking shipments.order_id back to orders.id."
    )
    target_description = (
        "orders contains metadata columns only, shipments contains shipping columns plus order_id, "
        "and both tables preserve all 200 rows."
    )
    max_steps = 20
    target_reward = 0.95

    def get_initial_observation_data(self):
        return {
            "focus_tables": ["orders", "shipments"],
            "seed_note": "orders starts with 200 mixed rows containing both metadata and shipping columns.",
            "task_goal": self.description,
        }

    def get_hint(self) -> str:
        return (
            "Step 1: CREATE TABLE shipments with order_id, address, city, postal_code, shipped_at. "
            "Step 2: INSERT shipping rows from orders into shipments. "
            "Step 3: Create a new orders table with metadata columns only. "
            "Step 4: Swap the new orders table into place and keep 200 rows in both tables."
        )

    def get_target_schema_requirements(self):
        return {
            "task_grader": "medium",
            "required_tables": ["orders", "shipments"],
            "required_row_counts": {"orders": 200, "shipments": 200},
        }
