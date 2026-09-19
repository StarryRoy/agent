import copy
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from procurement_agent.business_validation import (
    BusinessConsistencyError,
    build_execution_arguments,
    validate_business_result,
)
from procurement_agent.models import build_mock_execute_query_tool
from procurement_agent.schemas import SUBAGENT_OUTPUT_TYPES
from procurement_agent.services import ProcurementServices
from procurement_agent.test_runtime import ProcurementTestConfig, use_test_config
from procurement_agent.tool_contracts import (
    TOOL_CONTRACTS,
    Evidence,
    PricingArgs,
    ProcurementRequest,
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


def _test_config(**overrides):
    return {
        "simulate_sql_failure": False,
        "simulate_subagent_failure": False,
        "simulate_mcp_failure": False,
        "simulate_execution_failure": False,
        "simulate_atomic_failure": False,
        **overrides,
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
        assert "test_config" not in args_schema.model_fields
        serialized_schema = json.dumps(args_schema.model_json_schema())
        assert "test_config" not in serialized_schema
        assert "simulate_" not in serialized_schema
        for field in args_schema.model_fields.values():
            assert field.is_required()
            assert field.description


def test_each_subagent_has_a_strict_role_specific_output_schema():
    schemas = {
        name: TypeAdapter(output_type).json_schema()
        for name, output_type in SUBAGENT_OUTPUT_TYPES.items()
    }
    assert set(schemas) == {
        "requirement_agent",
        "inventory_agent",
        "supplier_agent",
        "pricing_agent",
        "budget_agent",
        "risk_agent",
        "execution_agent",
    }
    for schema in schemas.values():
        serialized = json.dumps(schema)
        assert '"remarks"' in serialized
        assert '"additionalProperties": false' in serialized
        assert '"source_ref"' not in serialized
    assert "recommended_purchase_quantity" in json.dumps(schemas["inventory_agent"])
    assert "candidate_suppliers" in json.dumps(schemas["supplier_agent"])
    assert "estimated_occupation" in json.dumps(schemas["budget_agent"])


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


def test_fault_injection_is_separate_from_business_request():
    assert not any(name.startswith("simulate_") for name in ProcurementRequest.model_fields)
    config = ProcurementTestConfig.model_validate(_test_config(simulate_sql_failure=True))
    tools = _tools()
    query_tool = build_mock_execute_query_tool("inventory")
    assert "simulate_" not in json.dumps(query_tool.args_schema.model_json_schema())
    with use_test_config(config):
        result = tools["parse_requirement"].invoke(
            {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
        )
        query_result = query_tool.invoke(
            {
                "sql": "__mock__",
                "request": result["request"],
                "analysis_strategy": "standard",
            }
        )
    assert not any(field.startswith("simulate_") for field in result["request"])
    assert "test_config" not in result
    assert query_result["ok"] is False
    assert query_result["error"]["type"] == "table_not_found"


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

    config = ProcurementTestConfig.model_validate(_test_config(simulate_execution_failure=True))
    with use_test_config(config):
        execution = tools["execute_procurement_plan"].invoke(execution_arguments)
    assert execution["status"] == "failed"
    assert execution["error"]["type"] == "scripted_execution_failure"


def test_complete_chain_passes_deterministic_business_consistency_validation():
    tools = _tools(SuccessfulWriteDatabase())
    fixtures = _fixtures()
    requirement = tools["parse_requirement"].invoke(
        {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
    )
    state = {"requirement": validate_business_result("requirement_agent", requirement, {})}
    request = requirement["request"]
    common = {"analysis_strategy": "standard", "replan_reason": None}
    inventory = tools["calculate_inventory"].invoke(
        {"request": request, "query_result": _query(fixtures["inventory"]["1"]), **common}
    )
    state["inventory_analysis"] = validate_business_result("inventory_agent", inventory, state)
    suppliers = tools["calculate_suppliers"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "query_result": _query(fixtures["supplier"]["1"]),
            **common,
        }
    )
    state["supplier_analysis"] = validate_business_result("supplier_agent", suppliers, state)
    pricing = tools["calculate_pricing"].invoke(
        {
            "request": request,
            "inventory_analysis": inventory,
            "supplier_analysis": suppliers,
            "query_result": _query(fixtures["pricing"]["1"]),
            **common,
        }
    )
    state["pricing_analysis"] = validate_business_result("pricing_agent", pricing, state)
    budget = tools["calculate_budget"].invoke(
        {
            "request": request,
            "pricing_analysis": pricing,
            "query_result": _query(fixtures["budget"]["IT"]),
            **common,
        }
    )
    state["budget_analysis"] = validate_business_result("budget_agent", budget, state)
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
    state["risk_analysis"] = validate_business_result("risk_agent", risk, state)
    execution_state = copy.deepcopy(state)
    execution_state["budget_analysis"]["remarks"] = ["仅供预算 Agent 展示"]
    strict_arguments = build_execution_arguments(execution_state, "contract-test")
    assert set(strict_arguments) == {
        "request",
        "recommended_plan",
        "budget_analysis",
        "session_id",
    }
    assert "remarks" not in strict_arguments["budget_analysis"]
    del execution_state["budget_analysis"]["effective_available_budget"]
    with pytest.raises(ValidationError):
        build_execution_arguments(execution_state, "contract-test")
    drifted_risk = copy.deepcopy(risk)
    drifted_risk["recommended_plan"]["quantity"] = 500
    with pytest.raises(BusinessConsistencyError, match="分配数量"):
        validate_business_result("risk_agent", drifted_risk, state)
    execution = tools["execute_procurement_plan"].invoke(
        {
            "request": request,
            "recommended_plan": risk["recommended_plan"],
            "budget_analysis": budget,
            "session_id": "contract-test",
        }
    )
    state["execution"] = validate_business_result("execution_agent", execution, state)
    drifted_execution = {**execution, "budget_reserved": 1}
    with pytest.raises(BusinessConsistencyError, match="执行预算占用"):
        validate_business_result("execution_agent", drifted_execution, state)

    assert state["inventory_analysis"]["recommended_purchase_quantity"] == 445
    assert state["risk_analysis"]["recommended_plan"]["quantity"] == 445
    assert state["execution"]["budget_reserved"] == risk["recommended_plan"]["total_cost"]


@pytest.mark.parametrize(
    ("agent_name", "mutate", "message"),
    [
        (
            "inventory_agent",
            lambda value: value.update(recommended_purchase_quantity=500),
            "建议采购数量",
        ),
        (
            "pricing_agent",
            lambda value: value["plans"][0]["allocations"][0].update(quantity=500),
            "分配数量",
        ),
        (
            "budget_agent",
            lambda value: value.update(estimated_occupation=1),
            "预算预计占用",
        ),
    ],
)
def test_consistency_validation_rejects_cross_stage_drift(agent_name, mutate, message):
    tools = _tools()
    fixtures = _fixtures()
    requirement = tools["parse_requirement"].invoke(
        {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
    )
    request = requirement["request"]
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
    state = {
        "requirement": requirement,
        "inventory_analysis": inventory,
        "supplier_analysis": suppliers,
        "pricing_analysis": pricing,
    }
    values = {
        "inventory_agent": copy.deepcopy(inventory),
        "pricing_agent": copy.deepcopy(pricing),
        "budget_agent": copy.deepcopy(budget),
    }
    mutate(values[agent_name])
    with pytest.raises(BusinessConsistencyError, match=message):
        validate_business_result(agent_name, values[agent_name], state)


def test_normal_business_request_has_no_simulation_fields_and_completes_analysis():
    tools = _tools()
    fixtures = _fixtures()
    request = tools["parse_requirement"].invoke(
        {"text": "下个月采购500台设备，预算80万。", "previous_request": None}
    )["request"]
    assert not any(field.startswith("simulate_") for field in request)

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
    assert risk["status"] == "success"


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
