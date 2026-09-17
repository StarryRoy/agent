"""Procurement database bootstrap from the canonical SQL catalog."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_harness import SQLiteBackend, SQLiteConfig

SQL_ROOT = Path(__file__).with_name("sql")
DATABASE_SETTINGS = SQL_ROOT / "settings.sql"

# SQLite permits a foreign key to reference a table created later. Keeping the
# purchase_requests schema last matters because it also defines execution triggers.
SCHEMA_ORDER = (
    "products",
    "departments",
    "budgets",
    "suppliers",
    "supplier_products",
    "quotations",
    "purchase_history",
    "supplier_quality_records",
    "supplier_delivery_records",
    "inventory",
    "inventory_history",
    "purchase_orders",
    "operation_logs",
    "purchase_requests",
)

SEED_ORDER = (
    "products",
    "departments",
    "budgets",
    "suppliers",
    "supplier_products",
    "quotations",
    "purchase_history",
    "supplier_quality_records",
    "supplier_delivery_records",
    "inventory",
    "inventory_history",
)


def read_sql_asset(table: str, filename: str) -> str:
    """Read a UTF-8 SQL asset after constraining it to the canonical catalog."""

    if table not in SCHEMA_ORDER or filename not in {"schema.sql", "seed.sql", "execute.sql"}:
        raise ValueError(f"unsupported SQL asset: {table}/{filename}")
    path = (SQL_ROOT / table / filename).resolve()
    path.relative_to(SQL_ROOT.resolve())
    return path.read_text(encoding="utf-8")


def read_database_settings() -> str:
    """Read connection-level SQLite settings kept outside Python source."""

    return DATABASE_SETTINGS.read_text(encoding="utf-8")


class ProcurementSQLiteBackend(SQLiteBackend):
    """Enable foreign keys while retaining Harness-owned transactions and cleanup."""

    def __init__(self, config: SQLiteConfig) -> None:
        super().__init__(config=config)
        try:
            self._connection.execute(read_database_settings())
        except Exception:
            self.close()
            raise


def initialize_database(path: str | Path, *, reset: bool = False) -> Path:
    """Create schema and demo data exclusively from files under ``sql/``."""

    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if reset and resolved.exists():
        resolved.unlink()
    connection = sqlite3.connect(resolved)
    try:
        connection.execute(read_database_settings())
        for table in SCHEMA_ORDER:
            connection.executescript(read_sql_asset(table, "schema.sql"))
        for table in SEED_ORDER:
            connection.executescript(read_sql_asset(table, "seed.sql"))
        connection.commit()
    finally:
        connection.close()
    return resolved
