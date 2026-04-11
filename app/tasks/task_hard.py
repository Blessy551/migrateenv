"""
Task 3 (Hard): v1 to v3 Multi-Table Migration

PRD Specification:
- Complex migration involving 5 tables: users, products, orders, order_items, reviews.
- Changes:
  1. Rename users.fullname -> users.first_name + users.last_name (split values).
  2. Type change: products.price (TEXT) -> NUMERIC(10,2).
  3. New table: discounts (id, order_id FK, amount, code).
  4. Index: partial index on orders(status) WHERE status != 'completed'.
- Starting: Baseline data in all 5 tables.
- Grading (0.35 + 0.25 + 0.15 + 0.10 + 0.10):
  * Schema diff score: 0.35
  * Data integrity (row counts): 0.25
  * users name split correctly: 0.15
  * products price coercion valid: 0.10
  * partial index present: 0.10
- Max steps: 30 | Time limit: 300s | Expected score: ~0.30
"""
from __future__ import annotations
from app.tasks.base import BaseTask

class HardTask(BaseTask):
    task_id = "hard"
    difficulty = "hard"
    description = "v1 to v3 migration: split users names, coerce product prices, add discounts table, and create partial indexes"
    max_steps = 30
    time_limit = 300
    target_reward = 0.30
    
    def get_target_description(self) -> str:
        return """Target Schema for Task 3:
        
users table:
- first_name, last_name (fullname must be split into these two)

products table:
- price column must be NUMERIC(10,2) and contain valid numeric data

discounts table (NEW):
- id (SERIAL PK), order_id (FK to orders.id), amount (DECIMAL), code (VARCHAR)

orders table:
- Existing columns preserved
- NEW: Partial index idx_orders_uncompleted on status WHERE status != 'completed'

Data Requirements:
- Row counts must be preserved for all tables.
- Full name 'First Last' must be split correctly into first_name='First', last_name='Last'."""

    def get_hint(self) -> str:
        return """Step-by-step migration plan:

Step 1: Split user names
ALTER TABLE users ADD COLUMN first_name TEXT, ADD COLUMN last_name TEXT;
UPDATE users SET first_name = split_part(fullname, ' ', 1), last_name = split_part(fullname, ' ', 2);
ALTER TABLE users DROP COLUMN fullname;

Step 2: Coerce product prices
ALTER TABLE products ALTER COLUMN price TYPE NUMERIC(10,2) USING price::numeric;

Step 3: Create discounts table
CREATE TABLE discounts (id SERIAL PRIMARY KEY, order_id INTEGER REFERENCES orders(id), amount NUMERIC(10,2), code TEXT);

Step 4: Create partial index
CREATE INDEX idx_orders_uncompleted ON orders(status) WHERE status != 'completed';"""

    def get_target_schema_requirements(self) -> dict:
        return {
            "required_tables": ["users", "products", "orders", "order_items", "reviews", "discounts"],
            "users": {"required_columns": ["id", "first_name", "last_name"], "removed_columns": ["fullname"]},
            "products": {"required_columns": ["id", "name", "price"]},
            "discounts": {
                "required_columns": ["id", "order_id", "amount", "code"],
                "foreign_keys": [{"column": "order_id", "ref_table": "orders", "ref_column": "id"}]
            },
            "orders": {
                "indexes": [{"name": "idx_orders_uncompleted", "partial": True}]
            },
            "data_checks": [
                {"table": "users", "check": "split_names"},
                {"table": "products", "check": "numeric_prices"},
                {"table": "users", "expected_count": 50},
                {"table": "products", "expected_count": 20},
                {"table": "orders", "expected_count": 100}
            ],
        }

    def reset_task(self, engine):
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS reviews CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS order_items CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS discounts CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS orders CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS products CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            
            # Create v1 schema
            conn.execute(text("CREATE TABLE users (id SERIAL PRIMARY KEY, fullname TEXT, email TEXT)"))
            conn.execute(text("CREATE TABLE products (id SERIAL PRIMARY KEY, name TEXT, price TEXT)"))
            conn.execute(text("CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), status TEXT, total NUMERIC)"))
            conn.execute(text("CREATE TABLE order_items (id SERIAL PRIMARY KEY, order_id INTEGER REFERENCES orders(id), product_id INTEGER REFERENCES products(id), quantity INTEGER)"))
            conn.execute(text("CREATE TABLE reviews (id SERIAL PRIMARY KEY, product_id INTEGER REFERENCES products(id), user_id INTEGER REFERENCES users(id), rating INTEGER, comment TEXT)"))
            
            # Seed data
            conn.execute(text("INSERT INTO users (fullname, email) VALUES " + ", ".join([f"('User {i} Last', 'user{i}@example.com')" for i in range(50)])))
            conn.execute(text("INSERT INTO products (name, price) VALUES " + ", ".join([f"('Product {i}', '{10.50 + i}')" for i in range(20)])))
            conn.execute(text("INSERT INTO orders (user_id, status, total) VALUES " + ", ".join([f"({(i%50)+1}, '{'completed' if i%2==0 else 'pending'}', {100.0*i})" for i in range(100)])))
