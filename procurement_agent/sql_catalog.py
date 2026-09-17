"""Load the narrow schema and business metadata context assigned to each role."""

from __future__ import annotations

from pathlib import Path

SQL_ROOT = Path(__file__).with_name("sql")

ROLE_TABLES: dict[str, tuple[str, ...]] = {
    "inventory": ("products", "inventory", "inventory_history"),
    "supplier": (
        "suppliers",
        "supplier_products",
        "quotations",
        "supplier_quality_records",
        "supplier_delivery_records",
    ),
    "pricing": ("suppliers", "quotations", "purchase_history"),
    "budget": ("departments", "budgets"),
    "risk": (
        "suppliers",
        "supplier_products",
        "supplier_quality_records",
        "supplier_delivery_records",
    ),
}


def role_database_context(role: str) -> str:
    """Return only the canonical table documents needed by one SubAgent."""

    sections: list[str] = []
    for table in ROLE_TABLES.get(role, ()):
        directory = SQL_ROOT / table
        schema = (directory / "schema.sql").read_text(encoding="utf-8").strip()
        metadata = (directory / "metadata.md").read_text(encoding="utf-8").strip()
        sections.append(f"## {table}/schema.sql\n\n```sql\n{schema}\n```\n\n{metadata}")
    if not sections:
        return ""
    return (
        "以下是本 SubAgent 唯一可用的数据库 Schema/Metadata。不要假设其他表或列存在：\n\n"
        + "\n\n".join(sections)
    )
