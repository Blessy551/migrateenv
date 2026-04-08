"""
MEDIUM TASK — Normalize the Northwind 'products' table by splitting
pricing and stock data into a new 'product_pricing' table.

Goal:
  1. CREATE TABLE product_pricing with:
       product_id (FK → products.product_id),
       unit_price REAL, units_in_stock INTEGER,
       units_on_order INTEGER, reorder_level INTEGER,
       discontinued INTEGER
  2. INSERT INTO product_pricing SELECT pricing cols FROM products
  3. Rebuild products table WITHOUT the 5 pricing columns
     (SQLite does not support DROP COLUMN reliably — use table rebuild)
  4. FOREIGN KEY from product_pricing.product_id → products.product_id
     declared inline in CREATE TABLE (SQLite style)

Success criteria:
  - product_pricing table exists with all 77 rows
  - FK from product_pricing → products is declared
  - products table NO LONGER has the 5 pricing columns
  - products row count still 77
"""
from app.tasks.base import BaseTask


class MediumTask(BaseTask):
    task_id = "medium"
    difficulty = "medium"
    description = (
        "Normalize the Northwind 'products' table by extracting pricing/stock columns "
        "(unit_price, units_in_stock, units_on_order, reorder_level, discontinued) "
        "into a new 'product_pricing' table with a foreign key back to products. "
        "All 77 product rows must be preserved in both tables."
    )
    target_description = (
        "product_pricing table exists (77 rows), FK product_pricing.product_id → products.product_id. "
        "products table no longer contains the 5 pricing columns."
    )
    max_steps = 20
    target_reward = 0.95

    def get_initial_observation_data(self):
        return {
            "focus_tables": ["products"],
            "northwind_note": (
                "products has 77 rows. Columns to move: "
                "unit_price, units_in_stock, units_on_order, reorder_level, discontinued."
            ),
            "task_goal": self.description,
        }

    def get_hint(self) -> str:
        return (
            "IMPORTANT: You are on SQLite. ALTER TABLE ... DROP COLUMN is unreliable on older SQLite versions. "
            "Use the table-rebuild pattern to remove columns safely. "
            "Step 1: Inspect PRAGMA table_info('products') to get all current columns. "
            "Step 2: CREATE TABLE product_pricing ("
            "product_id INTEGER PRIMARY KEY, "
            "unit_price REAL, units_in_stock INTEGER, units_on_order INTEGER, "
            "reorder_level INTEGER, discontinued INTEGER, "
            "FOREIGN KEY (product_id) REFERENCES products(product_id)); "
            "Step 3: INSERT INTO product_pricing SELECT product_id, unit_price, units_in_stock, "
            "units_on_order, reorder_level, discontinued FROM products; "
            "Step 4: CREATE TABLE products_new with ALL original products columns EXCEPT "
            "unit_price, units_in_stock, units_on_order, reorder_level, discontinued. "
            "Step 5: INSERT INTO products_new SELECT <remaining_cols> FROM products; "
            "Step 6: DROP TABLE products; "
            "Step 7: ALTER TABLE products_new RENAME TO products; "
            "Always verify row counts with SELECT COUNT(*) after each major step."
        )

    def get_target_schema_requirements(self):
        return {
            "required_tables": ["products", "product_pricing"],
            "product_pricing": {
                "required_columns": [
                    {"name": "product_id"},
                    {"name": "unit_price"},
                    {"name": "units_in_stock"},
                    {"name": "units_on_order"},
                    {"name": "reorder_level"},
                    {"name": "discontinued"},
                ],
                "required_foreign_keys": [
                    {
                        "from_table": "product_pricing",
                        "constrained_columns": ["product_id"],
                        "referred_table": "products",
                        "referred_columns": ["product_id"],
                    }
                ],
            },
            "products": {
                "removed_columns": [
                    "unit_price", "units_in_stock",
                    "units_on_order", "reorder_level", "discontinued"
                ],
            },
            "required_row_counts": {
                "products": 77,
                "product_pricing": 77,
            },
        }
