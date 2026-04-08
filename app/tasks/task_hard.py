"""
HARD TASK — Multi-table migration on Northwind 'orders' table.

Goal:
  1. ADD COLUMN order_status VARCHAR(20) NOT NULL DEFAULT 'pending'
  2. UPDATE order_status based on shipped_date:
       shipped_date IS NULL → 'pending'
       shipped_date IS NOT NULL → 'shipped'
  3. ADD CHECK CONSTRAINT chk_order_status (order_status IN ('pending','shipped','cancelled'))
     NOTE: SQLite requires a table rebuild to add CHECK constraints.
     Use CREATE TABLE orders_new (..., CHECK (...)), copy data, DROP old, RENAME new.
  4. CREATE COMPOSITE INDEX idx_orders_customer_date ON orders(customer_id, order_date)
  5. CREATE TABLE audit_log (
         log_id INTEGER PRIMARY KEY,   -- SQLite auto-increment; do NOT use SERIAL
         table_name VARCHAR(50),
         operation VARCHAR(20),
         performed_at DATETIME DEFAULT CURRENT_TIMESTAMP,  -- do NOT use NOW()
         note TEXT
     )
  6. INSERT a record into audit_log documenting this migration

Success criteria:
  - orders.order_status column exists (830 rows preserved)
  - CHECK constraint chk_order_status present
  - Composite index idx_orders_customer_date exists
  - audit_log table exists with ≥ 1 row
  - No FK violations
"""
from app.tasks.base import BaseTask


class HardTask(BaseTask):
    task_id = "hard"
    difficulty = "hard"
    description = (
        "Multi-table migration on the Northwind 'orders' table: "
        "(1) Add order_status VARCHAR(20) column computed from shipped_date, "
        "(2) Add CHECK constraint on order_status values, "
        "(3) Create composite index on (customer_id, order_date), "
        "(4) Create audit_log table and record the migration. "
        "All 830 original order rows must be preserved."
    )
    target_description = (
        "orders has order_status column (correct values), CHECK constraint, composite index. "
        "audit_log table exists with ≥ 1 row. 830 order rows preserved."
    )
    max_steps = 30
    target_reward = 0.95

    def get_initial_observation_data(self):
        return {
            "focus_tables": ["orders"],
            "northwind_note": (
                "orders has 830 rows. shipped_date is NULL for pending orders. "
                "employee_id FK → employees, customer_id FK → customers, ship_via FK → shippers."
            ),
            "task_goal": self.description,
        }

    def get_hint(self) -> str:
        return (
            "IMPORTANT: You are on SQLite. Three things that will fail if you use them: "
            "(1) ALTER TABLE ... ADD CONSTRAINT — not supported, use table rebuild instead. "
            "(2) SERIAL — not supported, use INTEGER PRIMARY KEY instead (auto-increments). "
            "(3) NOW() — not supported, use CURRENT_TIMESTAMP instead. "
            "Step 1: Inspect PRAGMA table_info('orders') to get all current columns. "
            "Step 2: ALTER TABLE orders ADD COLUMN order_status VARCHAR(20) NOT NULL DEFAULT 'pending'; "
            "Step 3: UPDATE orders SET order_status = CASE WHEN shipped_date IS NULL "
            "THEN 'pending' ELSE 'shipped' END; "
            "Step 4: Rebuild orders to add CHECK constraint — "
            "CREATE TABLE orders_new (...all original columns..., order_status VARCHAR(20) NOT NULL DEFAULT 'pending', "
            "CHECK (order_status IN ('pending','shipped','cancelled'))); "
            "Step 5: INSERT INTO orders_new SELECT * FROM orders; "
            "Step 6: DROP TABLE orders; "
            "Step 7: ALTER TABLE orders_new RENAME TO orders; "
            "Step 8: CREATE INDEX idx_orders_customer_date ON orders(customer_id, order_date); "
            "Step 9: CREATE TABLE audit_log ("
            "log_id INTEGER PRIMARY KEY, "
            "table_name VARCHAR(50), operation VARCHAR(20), "
            "performed_at DATETIME DEFAULT CURRENT_TIMESTAMP, note TEXT); "
            "Step 10: INSERT INTO audit_log (table_name, operation, note) VALUES "
            "('orders', 'MIGRATION', 'Added order_status column, CHECK constraint, composite index, audit_log');"
        )

    def get_target_schema_requirements(self):
        return {
            "table": "orders",
            "required_columns": [
                {
                    "name": "order_status",
                    "type_contains": "VARCHAR",
                    "nullable": False,
                }
            ],
            "required_check_constraints": [
                {
                    "name": "chk_order_status",
                    "sqltext_contains": "order_status",
                }
            ],
            "required_indexes": [
                {
                    "name": "idx_orders_customer_date",
                    "table": "orders",
                }
            ],
            "required_tables": ["orders", "audit_log"],
            "required_row_counts": {
                "orders": 830,
            },
            "audit_log_min_rows": 1,
            "required_status_values": {
                "query": "SELECT DISTINCT LOWER(order_status) FROM orders ORDER BY 1",
                "must_contain": ["pending", "shipped"],
            },
        }
