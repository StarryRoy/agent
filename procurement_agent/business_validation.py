"""Deterministic cross-stage validation for confirmed procurement results."""

from __future__ import annotations

from collections.abc import Mapping
from math import isclose
from typing import Any

from pydantic import TypeAdapter

from .schemas import SUBAGENT_OUTPUT_TYPES

FIELD_BY_AGENT = {
    "requirement_agent": "requirement",
    "inventory_agent": "inventory_analysis",
    "supplier_agent": "supplier_analysis",
    "pricing_agent": "pricing_analysis",
    "budget_agent": "budget_analysis",
    "risk_agent": "risk_analysis",
    "execution_agent": "execution",
}

STAGE_DEPENDENCIES = {
    "requirement_agent": (),
    "inventory_agent": ("requirement",),
    "supplier_agent": ("requirement", "inventory_analysis"),
    "pricing_agent": ("requirement", "inventory_analysis", "supplier_analysis"),
    "budget_agent": ("requirement", "pricing_analysis"),
    "risk_agent": (
        "requirement",
        "supplier_analysis",
        "pricing_analysis",
        "budget_analysis",
    ),
    "execution_agent": ("requirement", "risk_analysis", "budget_analysis"),
}

DOWNSTREAM_FIELDS = {
    "requirement_agent": (
        "inventory_analysis",
        "supplier_analysis",
        "pricing_analysis",
        "budget_analysis",
        "risk_analysis",
        "execution",
    ),
    "inventory_agent": (
        "supplier_analysis",
        "pricing_analysis",
        "budget_analysis",
        "risk_analysis",
        "execution",
    ),
    "supplier_agent": ("pricing_analysis", "budget_analysis", "risk_analysis", "execution"),
    "pricing_agent": ("budget_analysis", "risk_analysis", "execution"),
    "budget_agent": ("risk_analysis", "execution"),
    "risk_agent": ("execution",),
    "execution_agent": (),
}


class BusinessConsistencyError(ValueError):
    """A structurally valid result contradicts confirmed procurement state."""


def missing_dependencies(agent_name: str, state: Mapping[str, Any]) -> list[str]:
    return [
        name
        for name in STAGE_DEPENDENCIES.get(agent_name, ())
        if name not in state or state.get(name, {}).get("status") in {"error", "failed"}
    ]


def _close(left: float, right: float) -> bool:
    return isclose(float(left), float(right), abs_tol=0.01)


def _request(state: Mapping[str, Any]) -> Mapping[str, Any]:
    return state.get("requirement", {}).get("request", {})


def _purchase_quantity(state: Mapping[str, Any]) -> int:
    inventory = state.get("inventory_analysis", {})
    if "recommended_purchase_quantity" in inventory:
        return int(inventory["recommended_purchase_quantity"])
    return int(_request(state).get("quantity") or 0)


def _validate_plan(
    plan: Mapping[str, Any], expected_quantity: int, request: Mapping[str, Any]
) -> None:
    allocations = list(plan["allocations"])
    allocated = sum(int(item["quantity"]) for item in allocations)
    if allocated != int(plan["quantity"]):
        raise BusinessConsistencyError("方案分配数量合计与方案总数量不一致")
    calculated_cost = round(
        sum(float(item["unit_price"]) * int(item["quantity"]) for item in allocations), 2
    )
    if not _close(calculated_cost, float(plan["total_cost"])):
        raise BusinessConsistencyError("方案分配金额合计与方案总金额不一致")
    average = round(float(plan["total_cost"]) / int(plan["quantity"]), 2)
    if not _close(average, float(plan["average_unit_price"])):
        raise BusinessConsistencyError("方案平均单价与数量、总金额不一致")
    if bool(plan["meets_quantity"]) != (int(plan["quantity"]) == expected_quantity):
        raise BusinessConsistencyError("方案数量与 meets_quantity 标志不一致")
    max_lead = max(int(item["lead_time_days"]) for item in allocations)
    if max_lead != int(plan["max_lead_time_days"]):
        raise BusinessConsistencyError("方案最长交期与供应商分配不一致")
    user_budget = request.get("budget")
    if user_budget is not None and bool(plan["within_user_budget"]) != (
        float(plan["total_cost"]) <= float(user_budget)
    ):
        raise BusinessConsistencyError("方案预算标志与用户预算不一致")


def _same_plan(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    keys = ("plan_id", "quantity", "total_cost", "allocations")
    return all(left.get(key) == right.get(key) for key in keys)


def validate_business_result(
    agent_name: str, value: Any, state: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate one SubAgent result and its deterministic relations to upstream state."""

    adapter = TypeAdapter(SUBAGENT_OUTPUT_TYPES[agent_name])
    result = adapter.validate_python(value).model_dump(mode="json")
    if result.get("remarks") is None:
        result.pop("remarks", None)
    if result.get("status") in {"error", "failed"}:
        return result

    if agent_name == "inventory_agent":
        demand = int(_request(state)["quantity"])
        projected = max(
            0,
            int(result["current_available_quantity"])
            + int(result["in_transit_quantity"])
            - int(result["forecast_consumption"])
            - int(result["safety_stock"]),
        )
        shortfall = max(demand - projected, 0)
        if projected != int(result["projected_usable_quantity"]):
            raise BusinessConsistencyError("库存预计可用数量计算不一致")
        if shortfall != int(result["estimated_shortfall"]):
            raise BusinessConsistencyError("库存缺口与采购需求不一致")
        if shortfall != int(result["recommended_purchase_quantity"]):
            raise BusinessConsistencyError("库存缺口与建议采购数量不一致")

    elif agent_name == "supplier_agent":
        if int(result["required_quantity"]) != _purchase_quantity(state):
            raise BusinessConsistencyError("供应商所需数量与库存确认采购数量不一致")

    elif agent_name == "pricing_agent":
        expected = _purchase_quantity(state)
        plans = list(result["plans"])
        for plan in plans:
            _validate_plan(plan, expected, _request(state))
        costs = {item["plan_id"]: float(item["total_cost"]) for item in result["cost_differences"]}
        if len(costs) != len(plans) or any(
            plan["plan_id"] not in costs
            or not _close(costs[plan["plan_id"]], float(plan["total_cost"]))
            for plan in plans
        ):
            raise BusinessConsistencyError("价格方案成本对比与方案金额不一致")
        recommended = result.get("recommended_price_plan")
        if recommended is not None and not any(_same_plan(recommended, plan) for plan in plans):
            raise BusinessConsistencyError("价格推荐方案不在候选方案中")

    elif agent_name == "budget_agent":
        pricing = state["pricing_analysis"]
        plans = list(pricing["plans"])
        if not plans:
            raise BusinessConsistencyError("没有价格方案时不能确认预算结果")
        expected = min(float(plan["total_cost"]) for plan in plans)
        if not _close(expected, float(result["estimated_occupation"])):
            raise BusinessConsistencyError("预算预计占用与价格方案金额不一致")
        effective = float(result["effective_available_budget"])
        occupation = float(result["estimated_occupation"])
        if bool(result["within_budget"]) != (occupation <= effective):
            raise BusinessConsistencyError("预算是否充足标志与金额不一致")
        if not _close(max(occupation - effective, 0), float(result["over_budget_amount"])):
            raise BusinessConsistencyError("预算超额金额计算不一致")
        if not _close(max(effective - occupation, 0), float(result["adjustment_room"])):
            raise BusinessConsistencyError("预算余量计算不一致")

    elif agent_name == "risk_agent":
        pricing_plans = list(state["pricing_analysis"]["plans"])
        expected = _purchase_quantity(state)
        assessed = [
            *([result["recommended_plan"]] if result.get("recommended_plan") else []),
            *result["alternative_plans"],
            *result["not_recommended_plans"],
        ]
        for plan in assessed:
            _validate_plan(plan, expected, _request(state))
            if not any(_same_plan(plan, source) for source in pricing_plans):
                raise BusinessConsistencyError("风险评估方案与价格阶段方案不一致")
        feasible = [
            *([result["recommended_plan"]] if result.get("recommended_plan") else []),
            *result["alternative_plans"],
        ]
        if any(
            not plan["meets_quantity"] or int(plan["quantity"]) != expected for plan in feasible
        ):
            raise BusinessConsistencyError("风险推荐或备选方案未满足确认采购数量")

    elif agent_name == "execution_agent":
        plan = state["risk_analysis"].get("recommended_plan")
        if plan is None:
            raise BusinessConsistencyError("没有已确认推荐方案时禁止执行")
        if int(plan["quantity"]) != _purchase_quantity(state):
            raise BusinessConsistencyError("最终执行数量与库存确认采购数量不一致")
        if result.get("status") == "success":
            if int(result["purchase_order_count"]) != len(plan["allocations"]):
                raise BusinessConsistencyError("采购订单数与方案分配数不一致")
            if not _close(float(result["budget_reserved"]), float(plan["total_cost"])):
                raise BusinessConsistencyError("执行预算占用与最终方案金额不一致")
    return result
