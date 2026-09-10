from __future__ import annotations

from datetime import date

import pytest

from procurement_agent import create_procurement_app

TODAY = date(2026, 9, 10)
REQUEST = "下个月需要采购500台设备，预算80万，月底前必须到货。"


@pytest.fixture
def app(tmp_path):
    application = create_procurement_app(
        data_dir=tmp_path,
        reset_database=True,
        enable_mcp=False,
        today=TODAY,
    )
    try:
        yield application
    finally:
        application.close()


def test_database_contains_complete_business_schema(app):
    tables = app.database.list_tables()
    assert tables["ok"] is True
    assert {
        "products",
        "inventory",
        "inventory_history",
        "suppliers",
        "supplier_products",
        "quotations",
        "purchase_history",
        "purchase_requests",
        "purchase_orders",
        "departments",
        "budgets",
        "supplier_quality_records",
        "supplier_delivery_records",
        "operation_logs",
    }.issubset(tables["tables"])


def test_full_flow_pauses_then_executes_after_approval(app):
    proposal = app.submit(REQUEST, session_id="full-flow")
    assert proposal.status == "approval_required"
    assert proposal.data["inventory_analysis"]["recommended_purchase_quantity"] == 445
    assert len(proposal.data["recommended_plan"]["allocations"]) == 2

    executed = app.approve("full-flow")
    assert executed.status == "completed"
    assert executed.data["approval_status"] == "approved"
    assert executed.data["execution_status"] == "success"
    assert any(action["action"] == "reserve_budget" for action in executed.data["executed_actions"])
    requests = app.database.execute_query("SELECT status, approval_status FROM purchase_requests")
    assert requests["rows"] == [{"status": "ordered", "approval_status": "approved"}]
    assert len(app.database.execute_query("SELECT id FROM purchase_orders")["rows"]) == 2


def test_inventory_sufficient_short_circuits_other_agents(app):
    result = app.submit("下个月需要50台设备，预算20万。", session_id="no-buy")
    assert result.status == "completed"
    assert result.data["execution_status"] == "not_required"
    assert result.data["recommended_plan"]["plan_id"] == "NO-PURCHASE"
    assert app.metrics()["subagent_calls"] == 2


def test_missing_critical_requirement_returns_clarification(app):
    result = app.submit("预算80万，月底前必须到货。", session_id="missing")
    assert result.status == "completed"
    assert set(result.data["missing_fields"]) == {"product", "quantity"}
    assert result.data["execution_status"] == "not_started"


def test_reject_does_not_write_business_records(app):
    assert app.submit(REQUEST, session_id="reject").status == "approval_required"
    result = app.reject("reject")
    assert result.data["approval_status"] == "rejected"
    assert result.data["execution_status"] == "failed"
    assert app.database.execute_query("SELECT id FROM purchase_requests")["row_count"] == 0


def test_hitl_modification_reuses_session_and_replans(app):
    assert app.submit(REQUEST, session_id="edit").status == "approval_required"
    edited = app.modify("edit", "把数量改成400台，不要供应商A。")
    assert edited.status == "approval_required"
    assert edited.data["request"]["quantity"] == 400
    assert "SUP-A" in edited.data["request"]["excluded_suppliers"]
    assert all(
        allocation["supplier_code"] != "SUP-A"
        for allocation in edited.data["recommended_plan"]["allocations"]
    )


def test_all_quotes_over_budget_replans_then_blocks(app):
    result = app.submit("下个月需要500台设备，预算10万。", session_id="low-budget")
    assert result.status == "completed"
    assert result.data["execution_status"] == "blocked"
    assert result.data["replan_count"] >= 1
    assert app.database.execute_query("SELECT id FROM purchase_requests")["row_count"] == 0


@pytest.mark.parametrize("failure_flag", ["simulate_sql_failure", "simulate_subagent_failure"])
def test_analysis_failure_retries_then_blocks(app, failure_flag):
    text = (
        '{"text":"下个月需要500台设备，预算80万。","'
        + failure_flag
        + '":true}'
    )
    result = app.submit(text, session_id=failure_flag)
    assert result.status == "completed"
    assert result.data["execution_status"] == "blocked"
    assert result.data["replan_count"] == 1


def test_execution_failure_is_explicit_and_does_not_create_request(app):
    text = (
        '{"text":"下个月需要500台设备，预算80万。",'
        '"simulate_execution_failure":true}'
    )
    assert app.submit(text, session_id="execution-failure").status == "approval_required"
    result = app.approve("execution-failure")
    assert result.data["execution_status"] == "failed"
    assert app.database.execute_query("SELECT id FROM purchase_requests")["row_count"] == 0


def test_checkpoint_survives_application_restart(tmp_path):
    first = create_procurement_app(
        data_dir=tmp_path,
        reset_database=True,
        enable_mcp=False,
        today=TODAY,
    )
    try:
        assert first.submit(REQUEST, session_id="durable").status == "approval_required"
    finally:
        first.close()

    second = create_procurement_app(data_dir=tmp_path, enable_mcp=False, today=TODAY)
    try:
        result = second.approve("durable")
        assert result.data["execution_status"] == "success"
    finally:
        second.close()


def test_mcp_supplier_status_is_observed_and_can_fallback(tmp_path):
    application = create_procurement_app(
        data_dir=tmp_path,
        reset_database=True,
        enable_mcp=True,
        today=TODAY,
    )
    try:
        text = (
            '{"text":"下个月需要500台设备，预算80万。",'
            '"simulate_mcp_failure":true}'
        )
        result = application.submit(text, session_id="mcp-fallback")
        assert result.status == "approval_required"
        assert application.metrics()["mcp_calls"] >= 1
        assert any("MCP" in warning for warning in result.data["warnings"])
    finally:
        application.close()
