from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _schema_tables() -> set[str]:
    module = ast.parse((ROOT / "procurement_agent/database.py").read_text(encoding="utf-8"))
    schema = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(getattr(target, "id", None) == "SCHEMA_SQL" for target in node.targets)
    )
    return {
        line.removeprefix("CREATE TABLE IF NOT EXISTS ").split()[0]
        for line in schema.splitlines()
        if line.startswith("CREATE TABLE IF NOT EXISTS ")
    }


def test_every_business_table_has_schema_and_metadata() -> None:
    catalog = ROOT / "procurement_agent/database_catalog"
    tables = _schema_tables()
    assert {path.name for path in catalog.iterdir() if path.is_dir()} == tables
    for table in tables:
        schema = (catalog / table / "schema.sql").read_text(encoding="utf-8")
        metadata = (catalog / table / "metadata.md").read_text(encoding="utf-8")
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema
        assert "业务含义与取值规则" in metadata


def test_read_analysis_methods_do_not_embed_or_execute_sql() -> None:
    module = ast.parse((ROOT / "procurement_agent/services.py").read_text(encoding="utf-8"))
    class_node = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "ProcurementServices"
    )
    query_methods = {
        "analyze_inventory",
        "analyze_suppliers",
        "analyze_pricing",
        "analyze_budget",
        "analyze_risk",
    }
    for method in (
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name in query_methods
    ):
        source = ast.get_source_segment(
            (ROOT / "procurement_agent/services.py").read_text(encoding="utf-8"), method
        )
        assert source is not None
        assert "SELECT " not in source.upper()
        assert ".execute_query(" not in source
