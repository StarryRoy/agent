"""Focused checks for native skill loading and lossless context projection."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from agent_harness import ModelRequest, ToolRequest
from agent_harness.middleware import AgentExecution
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from procurement_agent import create_procurement_app
from procurement_agent.context import ProcurementContextMiddleware, task_view
from procurement_agent.middleware import (
    ProcurementBusinessToolMiddleware,
    ProcurementOrchestrationMiddleware,
)
from procurement_agent.test_runtime import current_test_config


def test_each_query_subagent_loads_only_its_domain_skill(tmp_path):
    app = create_procurement_app(
        data_dir=tmp_path, deterministic=True, enable_mcp=False, today=date(2026, 9, 10)
    )
    try:
        result = app.submit("下个月需要500台设备，预算80万。", session_id="skill-scenario")
        assert result.status == "approval_required"
        events = [
            json.loads(line)
            for line in (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        loads = [event for event in events if event["event_type"] == "skill.load"]
        expected = {
            "inventory_agent": "inventory-analysis@1.0.0",
            "supplier_agent": "supplier-analysis@1.0.0",
            "pricing_agent": "pricing-analysis@1.0.0",
            "budget_agent": "budget-analysis@1.0.0",
            "risk_agent": "risk-analysis@1.0.0",
        }
        for role, skill in expected.items():
            role_loads = [event for event in loads if event["agent_name"] == role]
            assert len(role_loads) == 1
            assert role_loads[0]["metadata"]["skills"] == [skill]
    finally:
        app.close()


@pytest.mark.parametrize(
    "text,skill",
    [
        ("下个月需要500台设备，预算10万。", "cost-optimization@1.0.0"),
        ("需要采购500台设备，预算80万，2026-09-15必须到货。", "delivery-recovery@1.0.0"),
        ("下个月紧急需要500台设备，预算80万。", "urgent-procurement@1.0.0"),
        (
            "下个月需要500台设备，预算80万，不要供应商A并排除供应商D。",
            "supplier-risk-review@1.0.0",
        ),
    ],
)
def test_replanning_skills_are_loaded_only_by_main_agent(tmp_path, text, skill):
    app = create_procurement_app(
        data_dir=tmp_path, deterministic=True, enable_mcp=False, today=date(2026, 9, 10)
    )
    try:
        app.submit(text, session_id="main-skill")
        events = [
            json.loads(line)
            for line in (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        loads = [event for event in events if event["event_type"] == "skill.load"]
        assert any(
            event["agent_name"] == "procurement_main_agent" and skill in event["metadata"]["skills"]
            for event in loads
        )
        assert not any(
            event["agent_name"] != "procurement_main_agent" and skill in event["metadata"]["skills"]
            for event in loads
        )
    finally:
        app.close()


def test_context_uses_persistent_state_and_never_rebuilds_it_from_tool_messages():
    budget = {
        "over_budget_amount": 123.45,
        "effective_available_budget": 1000,
        "status": "success",
        "conclusion": "超预算",
        "queries": [
            {
                "subtask": "department_budget",
                "query": "secret_raw_rows",
                "data": {"ok": True, "rows": [{"large": "x" * 10000}]},
                "facts": ["部门可用预算1000"],
            }
        ],
    }
    original = ToolMessage(content=json.dumps(budget), name="budget_agent", tool_call_id="budget-1")
    canonical_budget = {"status": "success", "over_budget_amount": 999.0}
    state = {
        "messages": [original],
        "procurement_results": {"budget_analysis": canonical_budget},
    }
    request = ModelRequest(AgentExecution("main", state, session_id="context"), state, [original], {})
    ProcurementContextMiddleware().before_model(request)
    view = str(request.messages)
    assert "secret_raw_rows" not in view
    assert "budget-1" in view and "123.45" in view
    assert "secret_raw_rows" in original.content
    task = task_view(
        "pricing_agent",
        {
            "request": {"budget": 1000},
            "budget_analysis": budget,
            "replan_reason": "all_suppliers_over_budget",
            "test_config": {
                "simulate_sql_failure": False,
                "simulate_subagent_failure": False,
                "simulate_mcp_failure": True,
                "simulate_execution_failure": False,
                "simulate_atomic_failure": False,
            },
        },
    )
    assert "budget_analysis" not in task and "pricing_analysis" not in task
    assert request.state["procurement_results"]["budget_analysis"] == canonical_budget
    assert "procurement_results" not in request.execution.metadata
    assert "procurement_context" not in task
    assert "test_config" not in task


def test_format_request_ends_with_user_message_for_gemini_structured_output():
    messages = [HumanMessage(content="采购500台设备"), AIMessage(content="最终采购方案")]
    request = ModelRequest(
        AgentExecution("main", {}, session_id="format-context"),
        {"messages": messages},
        list(messages),
        {},
        purpose="format",
        response_format={"type": "object"},
    )

    ProcurementContextMiddleware().before_model(request)

    assert isinstance(request.messages[-1], HumanMessage)
    assert request.messages[-2] == messages[-1]


def test_requirement_delegation_recovers_original_user_text_from_parent_execution():
    source_text = "下个月采购500台设备，预算80万，月底前到货。"
    user_message = json.dumps(
        {"text": source_text, "simulate_sql_failure": True}, ensure_ascii=False
    )
    execution = AgentExecution("main", {}, session_id="requirement-source")
    model_request = ModelRequest(
        execution,
        {"messages": [HumanMessage(content=user_message)]},
        [HumanMessage(content=user_message)],
        {},
    )
    ProcurementContextMiddleware(enable_test_config=True).before_model(model_request)
    tool_request = ToolRequest(
        execution,
        SimpleNamespace(name="requirement_agent"),
        {
            "task": json.dumps(
                {"text": "模型改写后的不完整文本", "procurement_context": {}},
                ensure_ascii=False,
            )
        },
        {},
        "requirement-call",
    )

    observed = {}

    def call_next(request):
        observed["config"] = current_test_config()
        return request

    middleware = ProcurementOrchestrationMiddleware(enable_test_config=True)
    middleware.wrap_tool_call(tool_request, call_next)

    task = json.loads(tool_request.arguments["task"])
    assert task["text"] == source_text
    assert "test_config" not in task
    assert observed["config"].simulate_sql_failure is True
    assert observed["config"].simulate_execution_failure is False
    assert current_test_config() is None


def test_subagent_dependency_gate_validates_the_whole_model_batch_from_round_start():
    state = {
        "procurement_results": {
            "requirement": {"request": {"quantity": 500, "budget": 800000}},
            "inventory_analysis": {
                "status": "success",
                "recommended_purchase_quantity": 445,
            },
        }
    }
    execution = AgentExecution("main", state, session_id="dependency-gate")
    middleware = ProcurementOrchestrationMiddleware()
    middleware.before_agent(execution)
    model_request = ModelRequest(execution, state, [], {})
    ProcurementContextMiddleware().before_model(model_request)
    response = AIMessage(
        content="",
        tool_calls=[
            {"name": "supplier_agent", "args": {"task": "{}"}, "id": "supplier-ready"},
            {"name": "pricing_agent", "args": {"task": "{}"}, "id": "pricing-too-early"},
            {"name": "load_skill", "args": {"name": "cost-optimization"}, "id": "ordinary"},
        ],
    )
    middleware.after_model(model_request, response)

    pricing = ToolRequest(
        execution,
        SimpleNamespace(name="pricing_agent"),
        {"task": json.dumps({"request": {"quantity": 999}})},
        {},
        "pricing-too-early",
        state,
    )
    pricing_executed = False

    def execute_pricing(_request):
        nonlocal pricing_executed
        pricing_executed = True

    observation = middleware.wrap_tool_call(pricing, execute_pricing)
    assert pricing_executed is False
    assert observation["error_type"] == "dependency_conflict"
    assert observation["missing_dependencies"] == ["supplier_analysis"]

    supplier = ToolRequest(
        execution,
        SimpleNamespace(name="supplier_agent"),
        {"task": json.dumps({"request": {"quantity": 999}})},
        {},
        "supplier-ready",
        state,
    )
    middleware.wrap_tool_call(supplier, lambda request: {"status": "error"})
    task = json.loads(supplier.arguments["task"])
    assert task["request"]["quantity"] == 500
    assert task["inventory_analysis"]["recommended_purchase_quantity"] == 445
    assert "procurement_context" not in task

    ordinary = ToolRequest(
        execution,
        SimpleNamespace(name="load_skill"),
        {"name": "cost-optimization"},
        {},
        "ordinary",
    )
    assert middleware.wrap_tool_call(ordinary, lambda _request: "executed") == "executed"


def test_business_tool_inputs_are_pinned_to_subagent_structured_task():
    confirmed = {
        "request": {"quantity": 445},
        "inventory_analysis": {"recommended_purchase_quantity": 445},
        "supplier_analysis": {"required_quantity": 445, "remarks": None},
        "analysis_strategy": "standard",
        "replan_reason": None,
    }
    execution = AgentExecution(
        "pricing_agent",
        {"messages": [HumanMessage(content=json.dumps(confirmed))]},
    )
    request = ToolRequest(
        execution,
        SimpleNamespace(name="calculate_pricing"),
        {
            "request": {"quantity": 500},
            "inventory_analysis": {"recommended_purchase_quantity": 500},
            "supplier_analysis": {"required_quantity": 500},
            "query_result": {"ok": True},
            "analysis_strategy": "standard",
            "replan_reason": None,
        },
        {},
        "pricing-calculator",
    )
    observed = {}

    def call_next(tool_request):
        observed.update(tool_request.arguments)
        return {"status": "success"}

    ProcurementBusinessToolMiddleware().wrap_tool_call(request, call_next)
    assert observed["request"]["quantity"] == 445
    assert observed["inventory_analysis"]["recommended_purchase_quantity"] == 445
    assert observed["supplier_analysis"]["required_quantity"] == 445
    assert "remarks" not in observed["supplier_analysis"]
    assert observed["query_result"] == {"ok": True}


def test_production_context_does_not_enable_fault_injection_metadata():
    execution = AgentExecution("main", {}, session_id="production-context")
    message = HumanMessage(
        content=json.dumps(
            {"text": "采购500台设备", "simulate_sql_failure": True}, ensure_ascii=False
        )
    )
    request = ModelRequest(execution, {"messages": [message]}, [message], {})

    ProcurementContextMiddleware().before_model(request)

    assert "procurement_test_config" not in execution.metadata
