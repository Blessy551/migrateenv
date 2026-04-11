"""
Task 2 (Medium): Split Orders Table into Orders + Shipments

PRD Specification:
- Split monolithic `orders` table into `orders` and `shipments`.
- orders: (id, user_id, total, status, created_at)
- shipments: (id, order_id, address, city, postal_code, shipped_at) + FK to orders.id
- Starting: 200 rows in orders.
- Grading (0.25 each):
  * orders schema correct
  * shipments schema + FK correct
  * 200 rows preserved in orders
  * 200 rows moved to shipments with correct FK
- Max steps: 20 | Time limit: 120s | Expected score: ~0.60
"""
from __future__ import annotations
from app.tasks.base import BaseTask

class MediumTask(BaseTask):
    task_id = "medium"
    difficulty = "medium"
    description = "Split the monolithic orders table into orders + shipments with a foreign key relationship"
    max_steps = 20
    time_limit = 120
    target_reward = 0.60
    
    def get_target_description(self) -> str:
        return """Target Schema for Task 2:
        
orders table MUST have:
- id, user_id, total, status, created_at

shipments table MUST have:
- id, order_id (FK to orders.id), address, city, postal_code, shipped_at

Data Requirements:
- All 200 rows from the original orders table must be preserved.
- Shipping information must be correctly moved to the new shipments table."""

    def get_hint(self) -> str:
        return """Step-by-step migration plan:

Step 1: Inspect current orders table
SELECT table_name, column_name FROM information_schema.columns WHERE table_name='orders';

Step 2: Create the shipments table
CREATE TABLE shipments (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    address TEXT,
    city TEXT,
    postal_code TEXT,
    shipped_at TIMESTAMP
);

Step 3: Move data to shipments
INSERT INTO shipments (order_id, address, city, postal_code, shipped_at)
SELECT id, address, city, postal_code, shipped_at FROM orders;

Step 4: Drop old columns from orders
ALTER TABLE orders DROP COLUMN address, DROP COLUMN city, DROP COLUMN postal_code, DROP COLUMN shipped_at;"""

    def get_target_schema_requirements(self) -> dict:
        return {
            "required_tables": ["orders", "shipments"],
            "orders": {
                "required_columns": ["id", "user_id", "total", "status", "created_at"],
                "removed_columns": ["address", "city", "postal_code", "shipped_at"],
            },
            "shipments": {
                "required_columns": ["id", "order_id", "address", "city", "postal_code", "shipped_at"],
                "foreign_keys": [
                    {
                        "column": "order_id",
                        "ref_table": "orders",
                        "ref_column": "id"
                    }
                ]
            },
            "data_checks": [
                {"table": "orders", "expected_count": 200},
                {"table": "shipments", "expected_count": 200}
            ],
        }

    def reset_task(self, engine):
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS shipments CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS orders CASCADE"))
            
            # Create monolithic orders table
            conn.execute(text("""
                CREATE TABLE orders (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    total DECIMAL(10,2) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    address TEXT,
                    city TEXT,
                    postal_code TEXT,
                    shipped_at TIMESTAMP
                )
            """))
            
            # Seed 200 orders
            conn.execute(text("""
                INSERT INTO orders (user_id, total, status, created_at, address, city, postal_code, shipped_at)
            """) + ",\n".join([
                f"(1, {10.50 + i}, 'completed', NOW(), 'Address {i}', 'City {i}', 'Zip {i}', NOW())"
                for i in range(200)
            ]))
