import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from procurement_agent.services import ProcurementServices
from procurement_agent.tool_contracts import TOOL_CONTRACTS


class SuccessfulWriteDatabase:
    def execute_write(self, sql, parameters):
        assert sql
        assert parameters["session_id"] == "contract-test"
        return {"ok": True, "last_insert_id": 101}


def _tools(database=None):
    return ProcurementServices(database=database, today=date(2026, 9, 10)).tools()


def test_every_business_tool_uses_its_explicit_pydantic_contract():
    tools = _tools()
    assert set(tools) == set(TOOL_CONTRACTS)
    for name, tool in tools.items():
        args_schema, output_schema = TOOL_CONTRACTS[name]
        assert tool.args_schema is args_schema
        assert tool.metadata["output_schema"] == output_schema.model_json_schema()
        assert "task" not in args_schema.model_fields
        assert "plan_json" not in args_schema.model_fields
        for field in args_schema.model_fields.values():
            assert field.is_required()
            assert field.description


def test_missing_required_tool_fields_fail_before_business_logic_runs():
    with pytest.raises(ValidationError):
        _tools()["parse_requirement"].invoke({"text": "采购500台设备"})

    with pytest.raises(ValidationError):
        _tools()["calculate_inventory"].invoke(
            {
                "request": {},
                "query_result": {},
                "analysis_strategy": "standard",
                "replan_reason": None,
            }
        )


def test_requirement_tool_returns_output_validated_by_declared_model():
    result = _tools()["parse_requirement"].invoke(
        {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
    )
    _, output_schema = TOOL_CONTRACTS["parse_requirement"]
    validated = output_schema.model_validate(result)
    assert validated.request.quantity == 500
    assert validated.request.budget == 800000


def test_all_analysis_tool_outputs_satisfy_their_declared_models():
    tools = _tools(SuccessfulWriteDatabase())
    fixtures = json.loads(
        (
            Path(__file__).parents[1] / "procurement_agent" / "fixtures" / "query_results.json"
        ).read_text(encoding="utf-8")
    )

    def query(rows):
        return {
            "ok": True,
            "operation": "execute_query",
            "columns": list(rows[0]),
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
        }

    request = tools["parse_requirement"].invoke(
        {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
    )["request"]
    common = {"analysis_strategy": "standard", "replan_reason": None}
    inventory = tools["calculate_inventory"].invoke(
        {"request": request, "query_result": query(fixtures["inventory"]["1"]), **common}
    )
    suppliers = tools["calculate_suppliers"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "query_result": query(fixtures["supplier"]["1"]),
            **common,
        }
    )
    pricing = tools["calculate_pricing"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "supplier_analysis": suppliers,
            "query_result": query(fixtures["pricing"]["1"]),
            **common,
        }
    )
    budget = tools["calculate_budget"].invoke(
        {
            "request": request,
            "pricing_analysis": pricing,
            "query_result": query(fixtures["budget"]["IT"]),
            **common,
        }
    )
    risk = tools["calculate_risk"].invoke(
        {
            "request": request,
            "supplier_analysis": suppliers,
            "pricing_analysis": pricing,
            "budget_analysis": budget,
            "query_result": query(fixtures["risk"]["1"]),
            **common,
        }
    )

    assert [inventory["status"], suppliers["status"], pricing["status"], budget["status"]] == [
        "success",
        "success",
        "success",
        "success",
    ]
    assert risk["status"] == "success"
    execution_arguments = {
        "request": request,
        "recommended_plan": risk["recommended_plan"],
        "budget_analysis": budget,
        "session_id": "contract-test",
    }
    execution = tools["execute_procurement_plan"].invoke(execution_arguments)
    assert execution["status"] == "success"
    assert execution["purchase_request_id"] == 101

    request["simulate_execution_failure"] = True
    execution = tools["execute_procurement_plan"].invoke(execution_arguments)
    assert execution["status"] == "failed"
    assert execution["error"]["type"] == "scripted_execution_failure"


def test_all_analysis_error_outputs_satisfy_their_declared_models():
    tools = _tools()
    success = json.loads(
        (
            Path(__file__).parents[1] / "procurement_agent" / "fixtures" / "query_results.json"
        ).read_text(encoding="utf-8")
    )

    def query(rows):
        return {
            "ok": True,
            "operation": "execute_query",
            "columns": list(rows[0]),
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
        }

    failure = {
        "ok": False,
        "operation": "execute_query",
        "error": {
            "type": "database_unavailable",
            "message": "database is unavailable",
            "retryable": True,
        },
    }
    request = tools["parse_requirement"].invoke(
        {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
    )["request"]
    common = {"analysis_strategy": "standard", "replan_reason": None}
    inventory = tools["calculate_inventory"].invoke(
        {"request": request, "query_result": query(success["inventory"]["1"]), **common}
    )
    suppliers = tools["calculate_suppliers"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "query_result": query(success["supplier"]["1"]),
            **common,
        }
    )
    pricing = tools["calculate_pricing"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "supplier_analysis": suppliers,
            "query_result": query(success["pricing"]["1"]),
            **common,
        }
    )
    budget = tools["calculate_budget"].invoke(
        {
            "request": request,
            "pricing_analysis": pricing,
            "query_result": query(success["budget"]["IT"]),
            **common,
        }
    )

    calls = {
        "calculate_inventory": {"request": request, "query_result": failure, **common},
        "calculate_suppliers": {
            "request": request,
            "inventory_analysis": inventory,
            "query_result": failure,
            **common,
        },
        "calculate_pricing": {
            "request": request,
            "inventory_analysis": inventory,
            "supplier_analysis": suppliers,
            "query_result": failure,
            **common,
        },
        "calculate_budget": {
            "request": request,
            "pricing_analysis": pricing,
            "query_result": failure,
            **common,
        },
        "calculate_risk": {
            "request": request,
            "supplier_analysis": suppliers,
            "pricing_analysis": pricing,
            "budget_analysis": budget,
            "query_result": failure,
            **common,
        },
    }
    for name, arguments in calls.items():
        assert tools[name].invoke(arguments)["status"] == "error"
