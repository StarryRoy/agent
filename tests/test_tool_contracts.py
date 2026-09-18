import copy
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from procurement_agent.services import ProcurementServices
from procurement_agent.tool_contracts import (
    TOOL_CONTRACTS,
    Evidence,
    PricingArgs,
    RiskArgs,
    SupplierAnalysis,
)


class SuccessfulWriteDatabase:
    def execute_write(self, sql, parameters):
        assert sql
        assert parameters["session_id"] == "contract-test"
        return {"ok": True, "last_insert_id": 101}


def _tools(database=None):
    return ProcurementServices(database=database, today=date(2026, 9, 10)).tools()


def _fixtures():
    return json.loads(
        (
            Path(__file__).parents[1] / "procurement_agent" / "fixtures" / "query_results.json"
        ).read_text(encoding="utf-8")
    )


def _query(rows):
    return {
        "ok": True,
        "operation": "execute_query",
        "columns": list(rows[0]),
        "rows": rows,
        "row_count": len(rows),
        "truncated": False,
    }


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
    fixtures = _fixtures()

    request = tools["parse_requirement"].invoke(
        {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
    )["request"]
    common = {"analysis_strategy": "standard", "replan_reason": None}
    inventory = tools["calculate_inventory"].invoke(
        {"request": request, "query_result": _query(fixtures["inventory"]["1"]), **common}
    )
    suppliers = tools["calculate_suppliers"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "query_result": _query(fixtures["supplier"]["1"]),
            **common,
        }
    )
    pricing = tools["calculate_pricing"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "supplier_analysis": suppliers,
            "query_result": _query(fixtures["pricing"]["1"]),
            **common,
        }
    )
    budget = tools["calculate_budget"].invoke(
        {
            "request": request,
            "pricing_analysis": pricing,
            "query_result": _query(fixtures["budget"]["IT"]),
            **common,
        }
    )
    risk = tools["calculate_risk"].invoke(
        {
            "request": request,
            "supplier_analysis": suppliers,
            "pricing_analysis": pricing,
            "budget_analysis": budget,
            "query_result": _query(fixtures["risk"]["1"]),
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
    success = _fixtures()

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
        {"request": request, "query_result": _query(success["inventory"]["1"]), **common}
    )
    suppliers = tools["calculate_suppliers"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "query_result": _query(success["supplier"]["1"]),
            **common,
        }
    )
    pricing = tools["calculate_pricing"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "supplier_analysis": suppliers,
            "query_result": _query(success["pricing"]["1"]),
            **common,
        }
    )
    budget = tools["calculate_budget"].invoke(
        {
            "request": request,
            "pricing_analysis": pricing,
            "query_result": _query(success["budget"]["IT"]),
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


def test_mcp_and_calculator_evidence_match_real_runtime_shapes():
    adapter = TypeAdapter(Evidence)
    mcp = adapter.validate_python(
        {
            "source": "external_supplier_status_mcp",
            "operation": "supplier_status",
            "subtask": "external_status_check",
            "suppliers_checked": ["SUP-A", "SUP-C"],
            "statuses": {"SUP-A": "operational", "SUP-C": "capacity_warning"},
        }
    )
    assert mcp.operation == "supplier_status"
    mcp_summary = adapter.validate_python(
        {
            "source": "external_supplier_status_mcp",
            "operation": "supplier_status",
            "subtask": "external_status_check",
            "row_count": 4,
            "truncated": False,
        }
    )
    assert mcp_summary.row_count == 4

    for source, subtask, row_count in (
        ("calculate_suppliers", "supplier_analysis", 5),
        ("calculate_pricing", "pricing_analysis", 4),
        ("calculate_budget", "budget_analysis", 1),
        ("calculate_risk", "risk_analysis", 4),
    ):
        evidence = adapter.validate_python(
            {
                "source": source,
                "operation": source,
                "subtask": subtask,
                "row_count": row_count,
                "truncated": False,
            }
        )
        assert evidence.source == source

    detailed_budget = adapter.validate_python(
        {
            "source": "calculate_budget",
            "operation": "calculate_budget",
            "subtask": "budget_analysis",
            "row_count": 1,
            "truncated": False,
            "budget_total": 2_000_000,
            "used_budget": 920_000,
            "approved_not_executed": 180_000,
            "available_budget": 900_000,
            "user_budget": 800_000,
            "effective_available_budget": 800_000,
            "estimated_occupation": 640_600,
            "within_budget": True,
            "over_budget_amount": 0,
            "adjustment_room": 159_400,
            "budget_risk": "low",
            "conclusion": "预算充足",
        }
    )
    assert detailed_budget.estimated_occupation == 640_600


def test_candidate_and_rejected_supplier_contracts_are_distinct():
    tools = _tools()
    fixtures = _fixtures()
    request = tools["parse_requirement"].invoke(
        {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
    )["request"]
    common = {"analysis_strategy": "standard", "replan_reason": None}
    inventory = tools["calculate_inventory"].invoke(
        {"request": request, "query_result": _query(fixtures["inventory"]["1"]), **common}
    )
    suppliers = tools["calculate_suppliers"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "query_result": _query(fixtures["supplier"]["1"]),
            **common,
        }
    )

    assert "selection_reasons" not in suppliers["rejected_suppliers"][0]
    SupplierAnalysis.model_validate(suppliers)

    invalid = copy.deepcopy(suppliers)
    invalid["candidate_suppliers"][0].pop("selection_reasons")
    with pytest.raises(ValidationError):
        SupplierAnalysis.model_validate(invalid)


def test_pricing_and_risk_accept_complete_real_upstream_results_once():
    tools = _tools()
    fixtures = _fixtures()
    common = {"analysis_strategy": "standard", "replan_reason": None}
    request = tools["parse_requirement"].invoke(
        {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
    )["request"]
    inventory = tools["calculate_inventory"].invoke(
        {"request": request, "query_result": _query(fixtures["inventory"]["1"]), **common}
    )
    suppliers = tools["calculate_suppliers"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "query_result": _query(fixtures["supplier"]["1"]),
            **common,
        }
    )
    suppliers["evidence"].extend(
        [
            {
                "source": "calculate_suppliers",
                "operation": "calculate_suppliers",
                "subtask": "supplier_analysis",
                "row_count": 5,
                "truncated": False,
            },
            {
                "source": "external_supplier_status_mcp",
                "operation": "supplier_status",
                "subtask": "external_status_check",
                "suppliers_checked": ["SUP-A", "SUP-B", "SUP-C", "SUP-D"],
                "statuses": {
                    "SUP-A": "operational",
                    "SUP-B": "operational",
                    "SUP-C": "capacity_warning",
                    "SUP-D": "operational",
                },
            },
        ]
    )
    pricing_arguments = {
        "request": request,
        "inventory_analysis": inventory,
        "supplier_analysis": suppliers,
        "query_result": _query(fixtures["pricing"]["1"]),
        **common,
    }
    PricingArgs.model_validate(pricing_arguments)
    pricing = tools["calculate_pricing"].invoke(pricing_arguments)
    pricing["evidence"].append(
        {
            "source": "calculate_pricing",
            "operation": "calculate_pricing",
            "subtask": "pricing_analysis",
            "row_count": len(pricing["plans"]),
            "truncated": False,
        }
    )
    budget = tools["calculate_budget"].invoke(
        {
            "request": request,
            "pricing_analysis": pricing,
            "query_result": _query(fixtures["budget"]["IT"]),
            **common,
        }
    )
    budget["evidence"].append(
        {
            "source": "calculate_budget",
            "operation": "calculate_budget",
            "subtask": "budget_analysis",
            "row_count": 1,
            "truncated": False,
        }
    )
    risk_arguments = {
        "request": request,
        "supplier_analysis": suppliers,
        "pricing_analysis": pricing,
        "budget_analysis": budget,
        "query_result": _query(fixtures["risk"]["1"]),
        **common,
    }
    RiskArgs.model_validate(risk_arguments)
    assert tools["calculate_risk"].invoke(risk_arguments)["status"] == "success"
