from __future__ import annotations

import json
from datetime import date

import pytest
from langchain_core.language_models import FakeListChatModel

from procurement_agent import create_procurement_app
from procurement_agent import factory as factory_module
from procurement_agent.evaluation import run_evaluation
from procurement_agent.models import DeterministicProcurementModel
from procurement_agent.services import ProcurementServices

TODAY = date(2026, 9, 10)
REQUEST = "下个月需要采购500台设备，预算80万，月底前必须到货。"


def _database_dump(application) -> tuple[str, ...]:
    """Inspect transactional effects without embedding query SQL in tests."""

    return tuple(application.database.backend._connection.iterdump())


def _inserted_rows(application, table: str) -> list[str]:
    prefix = f'INSERT INTO "{table}"'
    return [line for line in _database_dump(application) if line.startswith(prefix)]


class BindableFakeChatModel(FakeListChatModel):
    """Non-procurement model double used only to verify factory wiring."""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def app(tmp_path):
    application = create_procurement_app(
        data_dir=tmp_path,
        reset_database=True,
        enable_mcp=False,
        today=TODAY,
        deterministic=True,
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
    assert len(_inserted_rows(app, "purchase_requests")) == 1
    assert len(_inserted_rows(app, "purchase_orders")) == 2
    assert len(_inserted_rows(app, "operation_logs")) == 1
    assert "full-flow" in _inserted_rows(app, "operation_logs")[0]


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
    assert _inserted_rows(app, "purchase_requests") == []


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
    assert _inserted_rows(app, "purchase_requests") == []


@pytest.mark.parametrize(
    ("failure_flag", "strategy", "database_errors", "error_events"),
    [
        ("simulate_sql_failure", "schema_recovery", 1, 0),
        ("simulate_subagent_failure", "fallback_recovery", 0, 1),
    ],
)
def test_analysis_failure_changes_strategy_and_only_reruns_affected_agent(
    app, failure_flag, strategy, database_errors, error_events
):
    text = '{"text":"下个月需要500台设备，预算80万。","' + failure_flag + '":true}'
    result = app.submit(text, session_id=failure_flag)
    assert result.status == "approval_required"
    assert result.data["execution_status"] == "awaiting_approval"
    assert result.data["replan_count"] == 1
    metrics = app.metrics()
    assert metrics["database_errors"] == 0
    assert metrics["errors"] == error_events
    assert [item["strategy"] for item in metrics["replan_strategies"]] == [strategy]
    assert metrics["subagent_route"].count("inventory_agent") == 2
    assert metrics["subagent_route"].count("supplier_agent") == 1
    assert metrics["subagent_route"].count("pricing_agent") == 1
    if strategy == "schema_recovery":
        assert result.data["inventory_analysis"]["evidence"][0]["source"] == (
            "DatabaseToolkit.execute_query"
        )
    else:
        assert "15%" in result.data["inventory_analysis"]["fallback_basis"]


def test_execution_failure_is_explicit_and_does_not_create_request(app):
    text = '{"text":"下个月需要500台设备，预算80万。","simulate_execution_failure":true}'
    assert app.submit(text, session_id="execution-failure").status == "approval_required"
    result = app.approve("execution-failure")
    assert result.data["execution_status"] == "failed"
    assert _inserted_rows(app, "purchase_requests") == []


def test_checkpoint_survives_application_restart(tmp_path):
    first = create_procurement_app(
        data_dir=tmp_path,
        reset_database=True,
        enable_mcp=False,
        today=TODAY,
        deterministic=True,
    )
    try:
        assert first.submit(REQUEST, session_id="durable").status == "approval_required"
    finally:
        first.close()

    second = create_procurement_app(
        data_dir=tmp_path,
        enable_mcp=False,
        today=TODAY,
        deterministic=True,
    )
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
        deterministic=True,
    )
    try:
        text = '{"text":"下个月需要500台设备，预算80万。","simulate_mcp_failure":true}'
        result = application.submit(text, session_id="mcp-fallback")
        assert result.status == "approval_required"
        assert application.metrics()["mcp_calls"] >= 1
        assert any("MCP" in warning for warning in result.data["warnings"])
    finally:
        application.close()


def test_production_factory_uses_explicit_configured_model(tmp_path):
    configured_model = BindableFakeChatModel(responses=["{}"])
    application = create_procurement_app(
        data_dir=tmp_path,
        reset_database=True,
        enable_mcp=False,
        model=configured_model,
    )
    try:
        assert application.agent.definition.model is configured_model
        assert not isinstance(application.agent.definition.model, DeterministicProcurementModel)
        assert all(
            subagent.definition.model is configured_model
            for subagent in application.agent._subagents.values()
        )
        expected = {
            "inventory_agent": ("inventory-analysis", "inventory", "budgets"),
            "supplier_agent": ("supplier-analysis", "suppliers", "budgets"),
            "pricing_agent": ("pricing-analysis", "quotations", "inventory"),
            "budget_agent": ("budget-analysis", "budgets", "quotations"),
            "risk_agent": ("risk-analysis", "supplier_quality_records", "budgets"),
        }
        for name, (skill, related_table, unrelated_table) in expected.items():
            definition = application.agent._subagents[name].definition
            assert [item.name for item in definition.skills] == [skill]
            query_tool = next(item for item in definition.tools if item.name == "execute_query")
            assert query_tool.func.__self__ is application.database
            assert related_table in definition.instructions
            assert unrelated_table not in definition.instructions
    finally:
        application.close()


def test_production_factory_creates_one_default_gemini_for_all_agents(
    tmp_path, monkeypatch
):
    default_model = BindableFakeChatModel(responses=["{}"])
    constructor_calls = []

    def create_gemini(**kwargs):
        constructor_calls.append(kwargs)
        return default_model

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(factory_module, "ChatGoogleGenerativeAI", create_gemini)

    application = create_procurement_app(
        data_dir=tmp_path,
        reset_database=True,
        enable_mcp=False,
    )
    try:
        assert constructor_calls == [
            {
                "model": factory_module.GEMINI_MODEL_NAME,
                "api_key": "test-gemini-key",
                "thinking_budget": 0,
            }
        ]
        assert application.agent.definition.model is default_model
        assert application.agent.definition.runtime_config.timeout_seconds == 90
        assert all(
            subagent.definition.model is default_model
            for subagent in application.agent._subagents.values()
        )
        assert all(
            subagent.definition.runtime_config.timeout_seconds == 60
            for subagent in application.agent._subagents.values()
        )
    finally:
        application.close()


def test_role_model_overrides_default_gemini_only_for_its_role(tmp_path, monkeypatch):
    default_model = BindableFakeChatModel(responses=["{}"])
    supplier_model = BindableFakeChatModel(responses=["{}"])
    constructor_calls = []

    def create_gemini(**kwargs):
        constructor_calls.append(kwargs)
        return default_model

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(factory_module, "ChatGoogleGenerativeAI", create_gemini)

    application = create_procurement_app(
        data_dir=tmp_path,
        reset_database=True,
        enable_mcp=False,
        role_models={"supplier": supplier_model},
    )
    try:
        assert len(constructor_calls) == 1
        assert application.agent._subagents["supplier_agent"].definition.model is supplier_model
        assert application.agent._subagents["inventory_agent"].definition.model is default_model
        assert application.agent.definition.model is default_model
    finally:
        application.close()


def test_real_mode_without_model_or_gemini_key_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        create_procurement_app(
            data_dir=tmp_path,
            reset_database=True,
            enable_mcp=False,
        )


def test_deterministic_mode_never_constructs_gemini(tmp_path, monkeypatch):
    def unexpected_gemini(**kwargs):
        raise AssertionError(f"Gemini should not be created in deterministic mode: {kwargs}")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(factory_module, "ChatGoogleGenerativeAI", unexpected_gemini)
    application = create_procurement_app(
        data_dir=tmp_path,
        reset_database=True,
        enable_mcp=False,
        deterministic=True,
    )
    try:
        assert isinstance(application.agent.definition.model, DeterministicProcurementModel)
    finally:
        application.close()


def test_budget_replan_changes_pricing_strategy_and_reuses_valid_analysis(app):
    result = app.submit("下个月需要500台设备，预算10万。", session_id="strategy-budget")
    assert result.data["execution_status"] == "blocked"
    metrics = app.metrics()
    assert [item["strategy"] for item in metrics["replan_strategies"]] == ["cost_reduction"]
    assert metrics["subagent_route"].count("inventory_agent") == 1
    assert metrics["subagent_route"].count("supplier_agent") == 1
    assert metrics["subagent_route"].count("pricing_agent") == 2
    assert result.data["pricing_analysis"]["analysis_strategy"] == "cost_reduction"


def test_deadline_replan_uses_delivery_path_without_rerunning_inventory(app):
    result = app.submit(
        "需要采购500台设备，预算80万，2026-09-15必须到货。",
        session_id="strategy-deadline",
    )
    assert result.data["execution_status"] == "blocked"
    metrics = app.metrics()
    assert [item["strategy"] for item in metrics["replan_strategies"]] == ["delivery_first"]
    assert metrics["subagent_route"].count("inventory_agent") == 1
    assert metrics["subagent_route"].count("supplier_agent") == 2
    assert metrics["subagent_route"].count("pricing_agent") == 2
    assert result.data["pricing_analysis"]["analysis_strategy"] == "delivery_recovery"


def test_delivery_recovery_can_generate_a_materially_new_feasible_plan(app):
    result = app.submit(
        "需要采购500台设备，预算90万，2026-09-22必须到货。",
        session_id="strategy-delivery-solution",
    )
    assert result.status == "approval_required"
    assert result.data["replan_count"] == 1
    assert result.data["pricing_analysis"]["analysis_strategy"] == "delivery_recovery"
    assert result.data["recommended_plan"]["conditional"] is True
    assert result.data["recommended_plan"]["meets_deadline"] is True
    assert result.data["recommended_plan"]["max_lead_time_days"] <= 12


def test_execution_transaction_rolls_back_every_critical_write(app):
    text = '{"text":"下个月需要采购500台设备，预算80万。","simulate_atomic_failure":true}'
    assert app.submit(text, session_id="atomic-boundary").status == "approval_required"
    database_before = _database_dump(app)

    result = app.approve("atomic-boundary")

    assert result.data["execution_status"] == "failed"
    assert result.data["executed_actions"] == []
    assert _database_dump(app) == database_before


def test_business_connection_enforces_foreign_keys_and_recovers(app, tmp_path):
    proposal = app.submit(REQUEST, session_id="foreign-key")
    assert proposal.status == "approval_required"
    envelope = {**proposal.data, "session_id": "foreign-key"}
    envelope = json.loads(json.dumps(envelope))
    envelope["recommended_plan"]["allocations"][-1]["supplier_id"] = -1
    database_before = _database_dump(app)

    result = ProcurementServices(app.database, TODAY).execute_plan(json.dumps(envelope))

    assert result["status"] == "failed"
    assert result["error"]["database_error"]["type"] == "constraint_violation"
    assert result["executed_actions"] == []
    assert _database_dump(app) == database_before

    assert app.approve("foreign-key").data["execution_status"] == "success"
    assert len(_inserted_rows(app, "purchase_orders")) == 2


def test_database_metrics_count_each_real_toolkit_operation(app):
    assert app.submit(REQUEST, session_id="database-metrics").status == "approval_required"
    assert app.approve("database-metrics").data["execution_status"] == "success"
    metrics = app.metrics()
    assert metrics["database_calls"] == 1
    assert metrics["database_route"] == ["execute_write"]


def test_evaluation_reports_all_target_dimensions():
    report = run_evaluation(limit=2)
    result = report.as_dict()
    assert result["metrics"]["passed"] == 2
    assert result["metrics"]["invalid_calls"] == 0
    for dimension in (
        "routing_accuracy",
        "tool_use_accuracy",
        "parameter_extraction_accuracy",
        "final_plan_accuracy",
        "exception_recovery_rate",
        "zero_invalid_call_rate",
        "duration_budget_rate",
        "token_budget_rate",
    ):
        assert result["metrics"][dimension] == 1.0
