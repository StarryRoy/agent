"""Focused checks for native skill loading and lossless context projection."""

import json
from datetime import date

import pytest
from agent_harness import ModelRequest
from agent_harness.middleware import AgentExecution
from langchain_core.messages import ToolMessage

from procurement_agent import create_procurement_app
from procurement_agent.context import ProcurementContextMiddleware, task_view


@pytest.mark.parametrize(
    "text,skill,role",
    [
        ("下个月需要500台设备，预算10万。", "cost-optimization", "pricing_agent"),
        ("需要采购500台设备，预算80万，2026-09-15必须到货。", "delivery-recovery", "pricing_agent"),
        ("下个月紧急需要500台设备，预算80万。", "urgent-procurement", "supplier_agent"),
        ("下个月需要500台设备，预算80万。", "supplier-risk-review", "supplier_agent"),
    ],
)
def test_scenario_loads_native_skill(tmp_path, text, skill, role):
    app = create_procurement_app(
        data_dir=tmp_path, deterministic=True, enable_mcp=False, today=date(2026, 9, 10)
    )
    try:
        if skill == "supplier-risk-review":
            assert app.database.execute_write("UPDATE suppliers SET risk_level = 'critical'")["ok"]
            assert app.database.execute_write(
                "UPDATE supplier_quality_records SET passed_lots = 0, severe_incidents = 10"
            )["ok"]
        result = app.submit(text, session_id="skill-scenario")
        assert result.status in {"completed", "approval_required"}
        events = [
            json.loads(line)
            for line in (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        loads = [event for event in events if event["event_type"] == "skill.load"]
        assert any(
            event["agent_name"] == role and skill in event["metadata"]["skills"] for event in loads
        )
        if skill == "urgent-procurement":
            assert not any("cost-optimization" in event["metadata"]["skills"] for event in loads)
    finally:
        app.close()


def test_context_preserves_gap_plan_and_raw_checkpoint_message():
    budget = {
        "over_budget_amount": 123.45,
        "effective_available_budget": 1000,
        "status": "success",
        "conclusion": "超预算",
        "queries": [
            {
                "subtask": "department_budget",
                "query": "SELECT secret_raw_rows",
                "data": {"ok": True, "rows": [{"large": "x" * 10000}]},
                "facts": ["部门可用预算1000"],
            }
        ],
    }
    original = ToolMessage(content=json.dumps(budget), name="budget_agent", tool_call_id="budget-1")
    request = ModelRequest(
        AgentExecution("main", {}, session_id="context"), {"messages": [original]}, [original], {}
    )
    ProcurementContextMiddleware().before_model(request)
    view = str(request.messages)
    assert "secret_raw_rows" not in view
    assert "budget-1" in view and "123.45" in view
    assert "secret_raw_rows" in original.content
    plan = {"plan_id": "PLAN-1", "total_cost": 1123.45, "quantity": 10}
    task = task_view(
        "pricing_agent",
        {
            "request": {"budget": 1000},
            "budget_analysis": budget,
            "pricing_analysis": {"recommended_price_plan": plan},
            "replan_reason": "all_suppliers_over_budget",
        },
    )
    assert "budget_analysis" not in task and "pricing_analysis" not in task
    assert task["procurement_context"]["budget_gap"] == 123.45
    assert task["procurement_context"]["current_plan"] == plan
