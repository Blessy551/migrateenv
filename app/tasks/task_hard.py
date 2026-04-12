"""
HARD TASK - Apply a multi-table version upgrade.
"""
from app.tasks.base import BaseTask


class HardTask(BaseTask):
    task_id = "hard"
    difficulty = "hard"
    description = (
        "Upgrade a small commerce schema by splitting users.fullname into first_name and last_name, "
        "coercing product prices into a numeric column, adding a discounts table, and creating "
        "an index for non-completed orders without losing data."
    )
    target_description = (
        "users has first_name and last_name populated, products has numeric price data, discounts exists, "
        "and orders has an index for unfinished orders while seeded row counts remain intact."
    )
    max_steps = 30
    target_reward = 0.80

    def get_initial_observation_data(self):
        return {
            "focus_tables": ["users", "products", "orders", "discounts"],
            "seed_note": "users, products, orders, order_items, and reviews are preloaded for the upgrade scenario.",
            "task_goal": self.description,
        }

    def get_hint(self) -> str:
        return (
            "Step 1: Add users.first_name and users.last_name. "
            "Step 2: Backfill them from fullname. "
            "Step 3: Add products.price_new NUMERIC and cast existing price into it. "
            "Step 4: Create discounts and an index for unfinished orders. "
            "Step 5: Mark the migration complete with done."
        )

    def get_target_schema_requirements(self):
        return {
            "task_grader": "hard",
            "required_tables": ["users", "products", "orders", "discounts"],
            "required_row_counts": {"users": 50, "products": 20, "orders": 100},
        }
