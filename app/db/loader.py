"""
Database loader for deterministic task-specific seed data.
"""
import os
import logging
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is required"
    )

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


def _get_loader_engine(database_url: str):
    hostname = (urlparse(database_url).hostname or "").lower()
    is_local = hostname in {"localhost", "127.0.0.1", "host.docker.internal"}
    return create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 30,
            **({} if is_local else {"sslmode": "require"}),
        },
        echo=False,
    )


def _drop_public_tables(conn) -> None:
    tables = conn.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' ORDER BY tablename"
        )
    ).fetchall()
    for row in tables:
        conn.execute(text(f'DROP TABLE IF EXISTS "{row[0]}" CASCADE'))


def _seed_easy(conn) -> None:
    conn.execute(text("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """))
    # Single batched INSERT — one round trip instead of 50
    values = ", ".join(
        f"({i + 1}, 'user{i + 1}@example.com', NOW() - INTERVAL '{i} days')"
        for i in range(50)
    )
    conn.execute(text(f"INSERT INTO users (id, email, created_at) VALUES {values}"))


def _seed_medium(conn) -> None:
    conn.execute(text("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            total NUMERIC(10, 2) NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            address TEXT NOT NULL,
            city TEXT NOT NULL,
            postal_code TEXT NOT NULL,
            shipped_at TIMESTAMP NULL
        )
    """))
    statuses = ["pending", "processing", "completed"]
    # Single batched INSERT — one round trip instead of 200
    values = ", ".join(
        (
            f"({i + 1}, {(i % 50) + 1}, {round(20 + (i * 1.5), 2)}, "
            f"'{statuses[i % len(statuses)]}', NOW() - INTERVAL '{i % 30} days', "
            f"'{i + 1} Market Street', 'City {i % 10}', '100{i % 10}', "
            + (f"NOW() - INTERVAL '{(i % 20) + 1} days'" if i % 3 != 0 else "NULL")
            + ")"
        )
        for i in range(200)
    )
    conn.execute(text(
        f"INSERT INTO orders (id, user_id, total, status, created_at, address, city, postal_code, shipped_at) "
        f"VALUES {values}"
    ))


def _seed_hard(conn) -> None:
    conn.execute(text("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            fullname TEXT NOT NULL,
            email TEXT NOT NULL
        )
    """))
    conn.execute(text("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            price TEXT NOT NULL
        )
    """))
    conn.execute(text("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """))
    conn.execute(text("""
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            quantity INTEGER NOT NULL
        )
    """))
    conn.execute(text("""
        CREATE TABLE reviews (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            rating INTEGER NOT NULL
        )
    """))

    # All batched — 5 round trips total instead of 320+
    user_values = ", ".join(
        f"({i + 1}, 'User {i} Last', 'user{i + 1}@example.com')"
        for i in range(50)
    )
    conn.execute(text(f"INSERT INTO users (id, fullname, email) VALUES {user_values}"))

    product_values = ", ".join(
        f"({i + 1}, 'Product {i + 1}', '{10 + (i * 2):.2f}')"
        for i in range(20)
    )
    conn.execute(text(f"INSERT INTO products (id, name, price) VALUES {product_values}"))

    statuses = ["pending", "processing", "completed", "cancelled"]
    order_values = ", ".join(
        f"({i + 1}, {(i % 50) + 1}, '{statuses[i % len(statuses)]}', NOW() - INTERVAL '{i % 40} days')"
        for i in range(100)
    )
    conn.execute(text(f"INSERT INTO orders (id, user_id, status, created_at) VALUES {order_values}"))

    item_values = ", ".join(
        f"({i + 1}, {i + 1}, {(i % 20) + 1}, {(i % 4) + 1})"
        for i in range(100)
    )
    conn.execute(text(f"INSERT INTO order_items (id, order_id, product_id, quantity) VALUES {item_values}"))

    review_values = ", ".join(
        f"({i + 1}, {(i % 50) + 1}, {(i % 20) + 1}, {(i % 5) + 1})"
        for i in range(50)
    )
    conn.execute(text(f"INSERT INTO reviews (id, user_id, product_id, rating) VALUES {review_values}"))


def initialize_db(task_id: str, database_url: str = DATABASE_URL) -> None:
    engine = _get_loader_engine(database_url)
    try:
        with engine.begin() as conn:
            _drop_public_tables(conn)
            if task_id == "easy":
                _seed_easy(conn)
            elif task_id == "medium":
                _seed_medium(conn)
            elif task_id == "hard":
                _seed_hard(conn)
            else:
                raise ValueError(f"Unknown task_id '{task_id}'")
        logger.info("Seeded task database for %s", task_id)
    finally:
        engine.dispose()


def get_table_row_counts(database_url: str = DATABASE_URL) -> dict:
    engine = _get_loader_engine(database_url)
    counts = {}
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' ORDER BY tablename"
                )
            )
            tables = [row[0] for row in result.fetchall()]
            for table in tables:
                try:
                    r = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))
                    counts[table] = r.scalar()
                except Exception:
                    counts[table] = -1
    finally:
        engine.dispose()
    return counts